"""Optional mic button you hold with the laser to dictate.

Off by default (`window_mic` in the config). Two anchors
(`window_mic_anchor`):

* ``hud`` (default): one button at the bottom-right of the view, anchored
  head-relative beside the Steam toolbar. Always reachable, including while a
  PC desktop is streamed -- the streamed PC's windows are pixels in a scene
  texture, not SteamVR overlays, so per-window anchoring cannot find them.
* ``window``: one plate at the bottom-right of every floating (gamescope)
  window; hold it to dictate, release to type there.

WHY THIS IS NOT STRAIGHTFORWARD
    Three binding-layer problems, all proven on the Frame:

    1. ``IVROverlay::GetTransformForOverlayCoordinates`` takes ``HmdVector2_t``
       BY VALUE. ctypes on aarch64 does not pass that struct the way the
       runtime reads it, so calling it from Python returns a bogus matrix --
       the same pose for every overlay, insensitive to the coordinates given.
       ``overlay_point.so`` (built from ``overlay_point.c`` by install.sh)
       performs the call with a real by-value struct.

    2. pyopenvr is version-mismatched with the Frame's runtime (``--check``
       reports it cannot provide ``IVRSystem_023``). Overlay calls are made
       here straight through ``libopenvr_api.so`` by vtable index instead.

    3. Floating windows are ``VROverlayTransform_DashboardTab`` overlays, whose
       transform getters all raise ``OverlayError_WrongTransformType``. So the
       window quad is probed through the coordinates helper rather than read.

    Additionally the quad does NOT change when a window is resized, so a mic
    placed from the quad alone drifts off after a resize. ``WindowScale``
    follows resize through the vrcompositor scene-graph websocket.

Approach adapted from whisper-frame (MIT):
    https://github.com/groffta/whisper-frame
"""

import ctypes
import logging
import math
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

OPENVR_LIB = Path("/opt/steamvr/bin/linuxarm64/libopenvr_api.so")
HERE = Path(__file__).parent
SHIM = HERE / "overlay_point.so"

# IVROverlay interface this runtime exposes.
OVERLAY_VERSION = b"FnTable:IVROverlay_028"
VR_APPLICATION_OVERLAY = 2

# ETrackingUniverseOrigin
STANDING = 1

# VROverlayInputMethod
MOUSE_INPUT = 1

# VREvent_t as this runtime lays it out (whisper-frame polled 60-byte records
# successfully on the Frame; the fields we read are all in the first 24 bytes).
EVENT_SIZE = 60
MOUSE_MOVE = 300
MOUSE_DOWN = 301
MOUSE_UP = 302
FOCUS_ENTER = 303
FOCUS_LEAVE = 304
LEFT_BUTTON = 1

# VROverlayFlags bits. Without these the plate is invisible in the dashboard.
VISIBLE_IN_DASHBOARD = 1 << 15
SORT_WITH_NON_SCENE = 1 << 14
MAKE_INTERACTIVE_IF_VISIBLE = 1 << 16

# A press shorter than this latches recording on; a longer one is hold-to-talk.
CLICK_S = 0.30

# Controller cadences and caps (ported from the 1.4.2 line).
SCAN_S = 1.5        # rescan cadence for new/closed floating windows
MAX_MICS = 16       # cap on simultaneously live mic overlays
# The service's OverlayApi can go stale while its process lives on: after
# headset sleep / SteamVR session churn, FindOverlay keeps returning nothing
# for NEW windows while a fresh process sees them (observed 2026-11-09
# 18:33: the service never found window.58 until it was restarted). Heal by
# rebuilding the api after a long zero-window drought; mics are re-created
# from the persistent overlay keys on the next scan.
REBUILD_AFTER_S = 60.0
HEARTBEAT_S = 60.0  # alive-log cadence so silence is never ambiguous again
PULSE_S = 0.25      # one recording-ring animation phase
HOVER_TTL = 0.5     # a mouse-move keeps a mic highlighted this long
HIDE_AFTER_S = 1.0  # hide a mic that has been unplaceable this long
MAX_TAKE_S = 300.0  # safety cap: force-stop a take left open this long
ALPHA = 0.9
SORT_ORDER = 80

# Plate geometry. Texture is 5:3, so the plate is 5 cm by 3 cm.
PLATE_METERS = 0.05
# Live 2026-11-10: the plate belongs fully OUTSIDE the window's right edge.
# Tucked inside the corner it sat right next to the grab bar SteamVR draws
# under a floating window, so the user had to aim between two targets. This is
# the clear gap between the window's visible right edge and the plate's near
# edge (not a fraction of the quad - the probed quad is wider than the glass).
PLATE_OUT_GAP = 0.05
# Plate centre below the window's bottom edge; level with the corner was too
# high next to the grab bar.
PLATE_DROP = 0.06
# Toward the viewer along the facing axis. Floating windows are CURVED while
# the three-corner probe fits a FLAT quad; on that plane the plate - out past
# the edge - sank behind the curved glass ("about 2 inches behind the plane of
# the window"). Push it clearly out in front of the curved surface instead.
PLATE_FRONT = 0.105

# Head-anchored ("hud") mic: one always-visible button at the bottom-right of
# the view, head relative so it follows the user into a streamed PC desktop
# where the PC's windows are just pixels in a scene texture, not SteamVR
# overlays. Metres in the HMD frame: +x right, -y down, -z forward (same
# anchoring the status badge uses).
HUD_X = 0.30
HUD_Y = -0.33
HUD_Z = -0.80
HUD_SEQ = 0

# "bar" anchor: the Steam bar under the active window is fixed in the room and
# the active window sits just above it, so the bar's pose can be derived from
# the window's quad. The mic sits at the bar's right end, at bar centre height.
# Offsets measured live 2026-11-10 13:20 from the compositor frame.
BAR_RIGHT = 0.312   # metres right of the window centre (bar end + gap)
BAR_GAP_Y = 0.195   # metres below the window's bottom edge (bar centre)
BAR_FRONT = 0.212   # metres toward the viewer (flush with the bar's plane)
BAR_SEQ = 0
# The Steam bar is itself a readable Absolute overlay; its pose is what moves
# when the dock is dragged, so the plate is pinned to its right end.
DOCK_KEY = "valve.steam.gamepadui.bar"
DOCK_GAP = 0.10     # plate centre beyond the bar's right corner
DOCK_FRONT = 0.04   # toward the bar's facing (in front of the dock)
DOCK_DROP = 0.0508  # two inches below the bar: clear of a growing dock
# Gamescope exposes the app's own Qt windows too (the Settings dialog becomes a
# newest-seq window). Only landscape windows are candidates for the bar anchor;
# portrait windows (a phone-shaped app, the Settings dialog) are skipped.
BAR_MIN_ASPECT = 1.25

# Easing. Short, so the mic stays on the glass during a stepped sample.
SMOOTH_S = 0.01
DRAG_S = 0.01
SNAP_M = 0.35

TEX_W, TEX_H = 150, 90
SCAN_MAX = 4096
SCENE_HOST = ("127.0.0.1", 27062)

# gamescope floating-window overlay keyspace (proven on the Frame).
WINDOW_PREFIX = "gamescope.gamescope-0.window."

# IVROverlay vtable indices (IVROverlay_028).
FN = {
    "find": 0,
    "create": 1,
    "destroy": 3,
    "set_flag": 11,
    "set_color": 14,
    "set_alpha": 16,
    "set_sort": 20,
    "set_width": 22,
    "get_width": 23,
    "get_absolute": 34,
    "get_transform_type": 32,
    "set_absolute": 33,
    "show": 43,
    "hide": 44,
    "visible": 45,
    "coords": 46,
    "wait_frame": 47,
    "poll": 48,
    "set_input": 50,
    "get_mouse_scale": 51,
    "set_mouse_scale": 52,
    "set_raw": 62,
}


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def unavailable_reason():
    """Why the window mic cannot run, or None when it can.

    Checked before enabling anything so a missing helper or runtime degrades to
    "feature off" instead of an exception.
    """
    if not OPENVR_LIB.exists():
        return "SteamVR runtime not found"
    if not SHIM.exists():
        return "overlay_point.so was not built (needs gcc at install time)"
    return None


def available():
    return unavailable_reason() is None


# --------------------------------------------------------------------------
# Pure geometry helpers (unit-tested; no headset required)
# --------------------------------------------------------------------------

def quat_basis(qx, qy, qz, qw):
    """Local axes of a SteamVR quaternion as (right, up, facing).

    On a window anchor the third axis points toward the viewer.
    """
    right = (
        1 - 2 * (qy * qy + qz * qz),
        2 * (qx * qy + qz * qw),
        2 * (qx * qz - qy * qw),
    )
    up = (
        2 * (qx * qy - qz * qw),
        1 - 2 * (qx * qx + qz * qz),
        2 * (qy * qz + qx * qw),
    )
    facing = (
        2 * (qx * qz + qy * qw),
        2 * (qy * qz - qx * qw),
        1 - 2 * (qx * qx + qy * qy),
    )
    return right, up, facing


def ease(current, target, dt, seconds):
    """Exponential approach to target. A jump past SNAP_M snaps outright."""
    gap = math.sqrt(sum((target[i] - current[i]) ** 2 for i in range(3)))
    if gap > SNAP_M:
        return tuple(target)
    alpha = 1.0 - math.exp(-dt / seconds) if dt and seconds else 1.0
    return tuple(current[i] + (target[i] - current[i]) * alpha for i in range(3))


def resize_factor(scale_x, scale_y, scale_z, floor=0.05):
    """Resize factor for a window quad, from the panel's scale triple.

    The dashboard scene graph scales the panel on resize while the overlay quad
    stays fixed, so dividing the lateral scales by z recovers the factor on the
    quad. Returns None when z is degenerate or missing.
    """
    if not scale_z or scale_z < floor or not scale_x or not scale_y:
        return None
    return (scale_x / scale_z, scale_y / scale_z)


def quad_from_probe(lower_left, lower_right, upper_left):
    """Turn three probed corner matrices into a window quad.

    Each matrix is a flat 12-float row-major HmdMatrix34_t; translation lives in
    m[3], m[7], m[11]. Returns (width, height, right_u, up_u, facing), or None if
    the quad is too small to trust.
    """
    def translation(m):
        return (m[3], m[7], m[11])

    left = translation(lower_left)
    right_edge = translation(lower_right)
    top = translation(upper_left)

    right_vec = [right_edge[i] - left[i] for i in range(3)]
    up_vec = [top[i] - left[i] for i in range(3)]
    width = math.sqrt(sum(c * c for c in right_vec))
    height = math.sqrt(sum(c * c for c in up_vec))
    if width < 0.05 or height < 0.05:
        return None

    right_u = tuple(c / width for c in right_vec)
    up_u = tuple(c / height for c in up_vec)
    facing = (
        right_u[1] * up_u[2] - right_u[2] * up_u[1],
        right_u[2] * up_u[0] - right_u[0] * up_u[2],
        right_u[0] * up_u[1] - right_u[1] * up_u[0],
    )
    return width, height, right_u, up_u, facing


def corner_target(center, width, height, right_u, up_u, facing,
                  factor=(1.0, 1.0)):
    """Centre of the mic plate for a window quad.

    The plate hangs fully outside the bottom-right corner: its near edge is
    PLATE_OUT_GAP clear of the window's right edge, it sits PLATE_DROP below
    the bottom edge and PLATE_FRONT toward the viewer, so it clears the grab
    bar under the window and floats in front of the curved glass.
    """
    fx, fy = factor
    to_the_right = width * fx / 2 + PLATE_OUT_GAP + PLATE_METERS / 2
    downward = height * fy / 2 + PLATE_DROP
    return tuple(
        center[row]
        + right_u[row] * to_the_right
        - up_u[row] * downward
        + facing[row] * PLATE_FRONT
        for row in range(3)
    )


def bar_window(api, windows):
    """Kept for a possible future revisit; the app is dock-only since 2026-11-11."""
    """The seq of the landscape window the Steam bar sits under, or None.

    ``windows`` maps seq -> overlay handle. Prefer the newest landscape window;
    fall back to the newest window at all if none reports a usable size."""
    for seq in sorted(windows, reverse=True):
        scale = api.get_mouse_scale(windows[seq])
        if scale and scale[1] > 0 and scale[0] / scale[1] >= BAR_MIN_ASPECT:
            return seq
    return max(windows) if windows else None


def dock_from_corners(ll, lr, ul, extra=0.0):
    """(target, right_u, up_u, facing) for the plate right of the Steam bar.

    ``ll``, ``lr`` and ``ul`` are world-space corner positions probed from the
    bar overlay. The plate is kept UPRIGHT (world up) beside the bar's right
    corner, centred on the bar's height: aligning it to the bar's own tilted
    axes made it ride on top of the bar instead of beside it. Returns None
    when the corners are missing or degenerate (the bar reads zeros while it
    is down).
    """
    if not (ll and lr and ul):
        return None

    def norm(v):
        n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        return None if n < 1e-6 else tuple(c / n for c in v)

    right = norm((lr[0] - ll[0], 0.0, lr[2] - ll[2]))
    if not right:
        return None
    up = (0.0, 1.0, 0.0)
    facing = (right[1] * up[2] - right[2] * up[1],
              right[2] * up[0] - right[0] * up[2],
              right[0] * up[1] - right[1] * up[0])
    height = math.sqrt(sum((ul[i] - ll[i]) ** 2 for i in range(3)))
    target = tuple(lr[i] + up[i] * (height / 2)
                   + right[i] * (DOCK_GAP + PLATE_METERS / 2 + extra)
                   + facing[i] * DOCK_FRONT for i in range(3))
    # Drop one inch: a dock that grows to the right then never overlaps it.
    target = (target[0], target[1] - DOCK_DROP, target[2])
    return target, right, up, facing


def absolute_matrix(center, right_u, up_u, facing):
    """Build a flat 12-float standing-space matrix from a basis and a centre."""
    m = [0.0] * 12
    for row in range(3):
        m[row * 4 + 0] = right_u[row]
        m[row * 4 + 1] = up_u[row]
        m[row * 4 + 2] = facing[row]
        m[3 + 4 * row] = center[row]
    return m


# --------------------------------------------------------------------------
# Live OpenVR API (ctypes vtable binding + the coordinate shim)
# --------------------------------------------------------------------------

class _VREvent(ctypes.Structure):
    """The 40-byte VREvent_t prefix: header, then the data union at @16.

    VREvent_t is ``{uint32 type; uint32 device; float age; VREvent_Data_t
    data;}`` and the data union holds 64-bit members, so it is 8-byte aligned:
    it begins at @16, NOT @12. Inside it, VREvent_Mouse_t is ``{float x;
    float y; uint32 button;}`` - x@16, y@20, button@24. VREvent_Overlay_t puts
    overlayHandle at @16.

    Reading the fields sequentially (the old layout) pushed the button to @28,
    i.e. uninitialised memory: ``button & LEFT_BUTTON`` then passed only when
    that garbage happened to have bit 0 set, silently dropping every other
    laser press (live 2026-11-10, "I have to press a couple times").
    """

    _fields_ = [
        ("eventType", ctypes.c_uint32),           # @0
        ("trackedDeviceIndex", ctypes.c_uint32),  # @4
        ("eventAgeSeconds", ctypes.c_float),      # @8; union aligns to @16
        ("overlayHandle", ctypes.c_uint64),       # @16 (VREvent_Overlay_t)
        ("mouseButton", ctypes.c_uint32),         # @24 (VREvent_Mouse_t.button)
        ("_rest", ctypes.c_uint32 * 3),           # @28..39; sizeof stays 40
    ]


class OverlayApi:
    """Thin pythonic wrapper over libopenvr_api.so + overlay_point.so.

    Binds the IVROverlay_028 functions by vtable index (pyopenvr is
    version-mismatched with this runtime) and routes
    GetTransformForOverlayCoordinates through the native shim, because the
    HmdVector2_t by-value ABI cannot be expressed in ctypes on aarch64.
    """

    def __init__(self, lib_path=None, shim_path=None):
        self.lib = ctypes.CDLL(str(lib_path or OPENVR_LIB))
        # VR_InitInternal returns a 64-bit init token. Wrapping it in a
        # CFUNCTYPE with a c_uint32 restype TRUNCATES that token and the
        # runtime then SEGVs later (proven on the Frame 2026-11-09): set
        # argtypes/restype on the lib functions directly instead.
        err = ctypes.c_int(0)
        self.lib.VR_InitInternal.argtypes = [
            ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        self.lib.VR_InitInternal.restype = ctypes.c_void_p
        self.lib.VR_InitInternal(ctypes.byref(err), VR_APPLICATION_OVERLAY)
        if err.value != 0:
            raise RuntimeError(f"VR_InitInternal failed: {err.value}")
        self._inited = True

        self.lib.VR_GetGenericInterface.argtypes = [
            ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        self.lib.VR_GetGenericInterface.restype = ctypes.c_void_p
        iface_err = ctypes.c_int(0)
        table = self.lib.VR_GetGenericInterface(OVERLAY_VERSION,
                                                ctypes.byref(iface_err))
        if not table or iface_err.value != 0:
            self.close()
            raise RuntimeError(
                f"VR_GetGenericInterface({OVERLAY_VERSION!r}) failed: "
                f"{iface_err.value}")
        fns = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))

        def bind(idx, restype, *argtypes):
            return ctypes.CFUNCTYPE(restype, *argtypes)(fns[idx])

        u64 = ctypes.c_uint64
        f32 = ctypes.c_float
        i32 = ctypes.c_int
        self._find = bind(FN["find"], i32, ctypes.c_char_p, ctypes.POINTER(u64))
        self._create = bind(FN["create"], i32, ctypes.c_char_p,
                            ctypes.c_char_p, ctypes.POINTER(u64))
        self._destroy = bind(FN["destroy"], i32, u64)
        self._set_flag = bind(FN["set_flag"], i32, u64, i32,
                              ctypes.c_bool)
        self._set_alpha = bind(FN["set_alpha"], i32, u64, f32)
        self._set_sort = bind(FN["set_sort"], i32, u64, ctypes.c_uint32)
        self._set_width = bind(FN["set_width"], i32, u64, f32)
        self._set_absolute = bind(FN["set_absolute"], i32, u64, i32,
                                  ctypes.c_void_p)
        self._show = bind(FN["show"], i32, u64)
        self._hide = bind(FN["hide"], i32, u64)
        self._poll = bind(FN["poll"], ctypes.c_bool, u64,
                          ctypes.POINTER(_VREvent), ctypes.c_uint32)
        self._set_input = bind(FN["set_input"], i32, u64, i32)
        self._get_mscale = bind(FN["get_mouse_scale"], i32, u64,
                                ctypes.POINTER(_Vec2))
        self._set_mscale = bind(FN["set_mouse_scale"], i32, u64,
                                ctypes.POINTER(_Vec2))
        self._set_raw = bind(FN["set_raw"], i32, u64, ctypes.c_void_p,
                             ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32)
        self._get_tt = bind(FN["get_transform_type"], i32, u64,
                            ctypes.POINTER(i32))
        self._get_width = bind(FN["get_width"], i32, u64, ctypes.POINTER(f32))
        self._get_absolute = bind(FN["get_absolute"], i32, u64,
                                  ctypes.POINTER(i32), ctypes.POINTER(_Mat))

        self.glue = ctypes.CDLL(str(shim_path or SHIM))
        self.glue.frameassist_overlay_point.argtypes = [
            ctypes.c_void_p, u64, i32, f32, f32,
            ctypes.POINTER(_Mat)]
        self.glue.frameassist_overlay_point.restype = i32
        self._coords_fn = fns[FN["coords"]]

    def close(self):
        # Deliberately does NOT call VR_ShutdownInternal. libopenvr_api.so is
        # a process-wide singleton, so the window mic's VR_InitInternal shares
        # the runtime with the app's main SteamVR session; shutting this
        # connection down tears the main session's interface pointers out from
        # under it and the app SEGVs at once. Proven live: every "window mics
        # disabled" (Settings Mic toggle / anchor change) crashed the app
        # (coredumps 2026-11-10 14:42, 14:45, 15:30, 15:42, 16:20, 16:31).
        # WindowMic.destroy() already removes the overlays; the runtime is
        # released when the process exits.
        self._inited = False

    # -- pythonic surface used by WindowMic --------------------------------

    def find(self, key):
        h = ctypes.c_uint64(0)
        if self._find(key.encode(), ctypes.byref(h)) == 0 and h.value:
            return h.value
        return 0

    def create(self, key, name):
        h = ctypes.c_uint64(0)
        rc = self._create(key.encode(), name.encode(), ctypes.byref(h))
        if rc == 0 and h.value:
            return h.value
        if rc == 17:  # VROverlayError_KeyInUse: already ours, reuse it
            return self.find(key)
        raise RuntimeError(f"createOverlay({key!r}) rc={rc}")

    def destroy(self, handle):
        rc = self._destroy(ctypes.c_uint64(handle))
        # Fakes return None; the real FFI returns an int rc.
        if rc not in (0, None):
            raise RuntimeError(f"destroyOverlay rc={rc}")

    def _check(self, what, rc):
        if rc != 0:
            raise RuntimeError(f"{what} rc={rc}")

    def set_width(self, handle, meters):
        self._check("setOverlayWidthInMeters",
                    self._set_width(ctypes.c_uint64(handle), meters))

    def set_alpha(self, handle, alpha):
        self._check("setOverlayAlpha",
                    self._set_alpha(ctypes.c_uint64(handle), alpha))

    def set_sort(self, handle, order):
        self._check("setOverlaySortOrder",
                    self._set_sort(ctypes.c_uint64(handle), order))

    def set_flag(self, handle, bit, on):
        self._check("setOverlayFlag",
                    self._set_flag(ctypes.c_uint64(handle), bit, on))

    def set_input(self, handle, method):
        self._check("setOverlayInputMethod",
                    self._set_input(ctypes.c_uint64(handle), method))

    def set_mouse_scale(self, handle, w, h):
        v = _Vec2(float(w), float(h))
        self._check("setOverlayMouseScale",
                    self._set_mscale(ctypes.c_uint64(handle), ctypes.byref(v)))

    def get_width(self, handle):
        w = ctypes.c_float(0.0)
        if self._get_width(ctypes.c_uint64(handle), ctypes.byref(w)) == 0:
            return w.value
        return 0.0

    def get_absolute(self, handle):
        """The overlay's absolute transform as 12 row-major floats, or None."""
        origin = ctypes.c_int(0)
        m = _Mat()
        if self._get_absolute(ctypes.c_uint64(handle),
                              ctypes.byref(origin), ctypes.byref(m)) == 0:
            return list(m.m)
        return None

    def get_transform_type(self, handle):
        """The overlay's VRTransformType (0 = Absolute), or None."""
        t = ctypes.c_int(-1)
        if self._get_tt(ctypes.c_uint64(handle), ctypes.byref(t)) == 0:
            return t.value
        return None

    def get_mouse_scale(self, handle):
        v = _Vec2()
        if self._get_mscale(ctypes.c_uint64(handle),
                            ctypes.byref(v)) == 0:
            return (v.x, v.y)
        return None

    def set_raw(self, handle, rgba, w, h):
        buf = ctypes.create_string_buffer(bytes(rgba), len(rgba))
        self._check("setOverlayRaw",
                    self._set_raw(ctypes.c_uint64(handle), buf, w, h, 4))

    def show(self, handle):
        self._check("showOverlay", self._show(ctypes.c_uint64(handle)))

    def hide(self, handle):
        self._check("hideOverlay", self._hide(ctypes.c_uint64(handle)))

    def set_absolute(self, handle, origin, matrix12):
        m = _Mat()
        for i, value in enumerate(matrix12):
            m.m[i] = value
        self._check("setOverlayTransformAbsolute",
                    self._set_absolute(ctypes.c_uint64(handle), origin,
                                       ctypes.byref(m)))

    def coords(self, handle, x, y):
        """Overlay-local (x, y) -> 12-float standing matrix, or None."""
        m = _Mat()
        rc = self.glue.frameassist_overlay_point(
            self._coords_fn, ctypes.c_uint64(handle), STANDING,
            float(x), float(y), ctypes.byref(m))
        if rc != 0:
            return None
        return list(m.m)

    def poll(self, handle):
        """One pending event for this overlay as a dict, or None."""
        ev = _VREvent()
        if not self._poll(ctypes.c_uint64(handle), ctypes.byref(ev),
                          ctypes.sizeof(ev)):
            return None
        return {
            "type": int(ev.eventType),
            "handle": int(ev.overlayHandle),
            "button": int(ev.mouseButton),
        }


class _Vec2(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


class _Mat(ctypes.Structure):
    _fields_ = [("m", ctypes.c_float * 12)]


# --------------------------------------------------------------------------
# Controller (ported from the 1.4.2 line; positioning replaced with the
# shim-based three-corner probe proven 2026-11-03)
# --------------------------------------------------------------------------

class _Mic:
    __slots__ = ("seq", "key", "handle", "win_handle", "face", "last_move",
                 "pose", "hidden")

    def __init__(self, seq, key, handle, win_handle):
        self.seq = seq
        self.key = key
        self.handle = handle
        self.win_handle = win_handle
        self.face = None        # last (kind, hover, phase) painted
        self.last_move = 0.0
        self.pose = None        # last eased centre (3-tuple)
        self.hidden = False     # hidden because the window is unplaceable


class WindowMic:
    """Creates, positions and drives one mic overlay per floating window.

    ``api`` is any object with the OverlayApi surface (a fake in tests).
    ``on_command(cmd, seq)`` receives "talk_start"/"talk_stop".
    ``can_act()`` lets the owner refuse new takes; ``engine_state()``
    feeds the busy face.
    """

    def __init__(self, api, app_key, on_command=None, can_act=None,
                 engine_state=None, clock=time.monotonic, enabled=True,
                 api_factory=None, hud=None, anchor="window",
                 dock_extra=0.0):
        self.api = api
        # ``anchor`` picks where the one optional button lives: "hud" (head
        # relative; ``hud(handle)`` sets the transform), "bar" (beside the
        # Steam bar under the active window, world locked) or "window" (a plate
        # on each floating window).
        self.anchor = anchor
        self.hud = hud
        self.dock_extra = float(dock_extra)
        self.app_key = app_key
        self.on_command = on_command or (lambda cmd, seq: None)
        self.can_act = can_act or (lambda: True)
        self.engine_state = engine_state or (lambda: None)
        self.clock = clock
        self.enabled = bool(enabled)
        self.api_factory = api_factory or OverlayApi
        self._mics = {}            # seq -> _Mic
        self._last_scan = 0.0
        self._seen_logged = None   # last window set we logged
        self._pos_warned = {}      # seq -> exception type already warned
        self._probe_fail_since = {}  # seq -> first failed probe time
        self._take_seq = None
        self._take_since = 0.0
        self._pressed_at = None
        self._last_tick = None
        self._bar_seq = None       # window the bar mic is anchored to
        self._dock_logged = False
        self.dock_extra = float(dock_extra)  # metres further right
        self.settings_open = False # freeze the anchor while Settings is open
        self._frozen_logged = False
        self._empty_since = None   # first scan of the current zero-window run
        self._last_beat = None     # last heartbeat log
        self.api_suspect = False   # set after a zero-window drought

    # -- lifecycle ---------------------------------------------------------

    def set_enabled(self, on):
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        if not on:
            self._stop_take()
            self._destroy_all()

    def destroy(self):
        self._stop_take()
        self._destroy_all()

    # -- main tick ---------------------------------------------------------

    def tick(self, now):
        if not self.enabled:
            return
        dt = None if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.anchor == "hud":
            steps = (self._ensure_hud, self._poll_events, self._repaint,
                     self._enforce_cap)
        elif self.anchor == "bar":
            steps = (self._sync_bar, self._poll_events, self._repaint,
                     self._enforce_cap)
        else:
            steps = (self._maybe_scan, self._poll_events, self._position,
                     self._repaint, self._enforce_cap)
        for step in steps:
            try:
                step(now, dt)
            except Exception as err:
                log.warning("floating mics: %s failed: %s",
                            getattr(step, "__name__", step), err)

    # -- head-anchored mic -------------------------------------------------

    def _ensure_hud(self, now, dt):
        if self._mics:
            return
        key = f"{self.app_key}.mic.hud"
        handle = self.api.find(key) or self.api.create(key, "FrameAssist mic")
        self._configure(handle)
        mic = _Mic(HUD_SEQ, key, handle, 0)
        self._upload(mic)
        self.api.show(handle)
        try:
            self.hud(handle)
        except Exception as err:
            log.warning("window mic: could not anchor the toolbar mic to the "
                        "head: %s", err)
        self._mics[HUD_SEQ] = mic
        log.info("window mic: toolbar mic shown")

    # -- Steam-bar mic -----------------------------------------------------

    def _sync_bar(self, now, dt):
        """Keep ONE mic at the right end of the Steam bar.

        The bar is an Absolute overlay (valve.steam.gamepadui.bar): its own
        pose is the only thing that moves when the dock is dragged, so the
        plate is pinned to its right end. Dock-only: when the bar can't be
        read the plate hides; it is never placed from a window."""
        if now - self._last_scan < SCAN_S:
            return
        self._last_scan = now
        mic = self._mics.get(BAR_SEQ)

        matrix = self._dock_matrix()
        win_handle = None
        # Dock-only (decided 2026-11-11): the plate is never derived from a
        # window. If the bar overlay can't be read (down, or not loaded yet),
        # hide the plate instead of parking it on a floating window. The old
        # window-derived fallback (`bar_window()` / `_bar_matrix()` below) is
        # kept for a possible future revisit but is no longer called.
        if matrix is None:
            if mic is not None and not mic.hidden:
                self.api.hide(mic.handle)
                mic.hidden = True
            self._dock_logged = False
            return
        if not self._dock_logged:
            self._dock_logged = True
            log.info("window mic: bar anchored to the Steam bar overlay")

        if mic is None:
            key = f"{self.app_key}.mic.bar"
            handle = self.api.find(key) or self.api.create(
                key, "FrameAssist mic")
            self._configure(handle)
            mic = _Mic(BAR_SEQ, key, handle, win_handle)
            self._upload(mic)
            self._mics[BAR_SEQ] = mic
            log.info("window mic: Steam-bar mic shown")
        mic.win_handle = win_handle
        self.api.set_absolute(mic.handle, STANDING, matrix)
        self.api.show(mic.handle)
        mic.hidden = False

    def _dock_matrix(self):
        """Standing-space transform for the plate on the Steam bar, or None."""
        handle = self.api.find(DOCK_KEY)
        if not handle:
            return None
        scale = self.api.get_mouse_scale(handle)
        if not scale:
            return None
        mx, my = scale
        ll = self.api.coords(handle, 0.0, 0.0)
        lr = self.api.coords(handle, float(mx), 0.0)
        ul = self.api.coords(handle, 0.0, float(my))
        pose = dock_from_corners(
            None if ll is None else (ll[3], ll[7], ll[11]),
            None if lr is None else (lr[3], lr[7], lr[11]),
            None if ul is None else (ul[3], ul[7], ul[11]),
            self.dock_extra)
        if pose is None:
            return None
        return absolute_matrix(*pose)

    def _bar_matrix(self, win_handle):
        """Standing-space transform for the mic centre at the bar's right
        end, derived from the window's quad."""
        scale = self.api.get_mouse_scale(win_handle)
        mx, my = scale if scale else (1920.0, 1080.0)
        ll = self.api.coords(win_handle, 0.0, 0.0)
        lr = self.api.coords(win_handle, float(mx), 0.0)
        ul = self.api.coords(win_handle, 0.0, float(my))
        if not (ll and lr and ul):
            return None
        quad = quad_from_probe(ll, lr, ul)
        if quad is None:
            return None
        _width, height, right_u, up_u, facing = quad
        centre = [ll[3 + 4 * i]
                  + (lr[3 + 4 * i] - ll[3 + 4 * i]) / 2
                  + (ul[3 + 4 * i] - ll[3 + 4 * i]) / 2
                  for i in range(3)]
        target = [
            centre[row]
            + right_u[row] * BAR_RIGHT
            - up_u[row] * (height / 2 + BAR_GAP_Y)
            + facing[row] * BAR_FRONT
            for row in range(3)
        ]
        return absolute_matrix(target, right_u, up_u, facing)

    # -- window scanning ---------------------------------------------------

    def _scan_windows(self):
        """All (seq, handle) for floating windows. NO visibility filter:
        live windows on this runtime report isOverlayVisible == False even
        while on screen (proven 2026-11-03)."""
        out = {}
        for seq in range(1, SCAN_MAX + 1):
            handle = self.api.find(f"{WINDOW_PREFIX}{seq}")
            if handle:
                out[seq] = handle
        return out

    def _maybe_scan(self, now, dt):
        if now - self._last_scan < SCAN_S:
            return
        self._last_scan = now
        if self._last_beat is None or now - self._last_beat >= HEARTBEAT_S:
            self._last_beat = now
            log.info("floating mics: alive, windows=%s, mics=%s",
                     sorted(self._seen_logged or []) or "none",
                     len(self._mics))
        windows = self._scan_windows()
        seen = set(windows)
        if seen != self._seen_logged:
            log.info("floating mics: gamescope windows: %s",
                     sorted(seen) or "none")
            self._seen_logged = set(seen)

        if not windows:
            # Stale-api watchdog: a healthy runtime with no windows floating
            # looks identical to a dead api, so after a long drought flag the
            # api as suspect. When windows ARE present the api is
            # demonstrably alive and we never flag it.
            if self._empty_since is None:
                self._empty_since = now
            elif now - self._empty_since >= REBUILD_AFTER_S:
                self._rebuild_api(now)
                return
        else:
            self._empty_since = None

        keep = {s: m for s, m in self._mics.items() if s in windows}
        for seq, handle in windows.items():
            if len(keep) >= MAX_MICS:
                break
            if seq not in keep:
                try:
                    keep[seq] = self._create_mic(seq, handle)
                    log.info("floating mics: created mic for window.%s "
                             "(overlay handle %s)", seq, keep[seq].handle)
                except Exception as err:
                    log.warning("floating mics: could not create mic for "
                                "window.%s: %s", seq, err)
        for seq in list(self._mics):
            if seq not in keep:
                self._remove_mic(seq)
                log.info("floating mics: removed mic for window.%s", seq)
        self._mics = keep

    def _rebuild_api(self, now):
        """Flag the overlay api as suspect after REBUILD_AFTER_S of
        continuous zero-window scans. The api is NOT rebuilt here: calling
        VR_InitInternal a second time while the owning session is live
        SEGVs the runtime (live 2026-11-09 19:15-19:18, one crash per
        rebuild). Instead the flag tells the owner (steamvr session loop)
        to cycle the session at a safe point, which re-creates this
        controller with a fresh api through the normal teardown path."""
        if self.api_suspect:
            return
        self.api_suspect = True
        self._empty_since = now
        log.info("floating mics: no windows for %.0fs - overlay api flagged "
                 "suspect; the next session cycle will rebuild it",
                 REBUILD_AFTER_S)

    def _create_mic(self, seq, win_handle):
        key = f"{self.app_key}.mic.{seq}"
        handle = self.api.find(key) or self.api.create(
            key, f"FrameAssist mic {seq}")
        self._configure(handle)
        mic = _Mic(seq, key, handle, win_handle)
        self._upload(mic)
        self.api.show(handle)
        return mic

    def _configure(self, handle):
        self.api.set_width(handle, PLATE_METERS)
        self.api.set_alpha(handle, ALPHA)
        self.api.set_sort(handle, SORT_ORDER)
        self.api.set_input(handle, MOUSE_INPUT)
        self.api.set_mouse_scale(handle, TEX_W, TEX_H)
        for bit in (VISIBLE_IN_DASHBOARD, SORT_WITH_NON_SCENE,
                    MAKE_INTERACTIVE_IF_VISIBLE):
            self.api.set_flag(handle, bit, True)

    def _remove_mic(self, seq):
        mic = self._mics.pop(seq, None)
        if mic is None:
            return
        try:
            self.api.destroy(mic.handle)
        except Exception as err:
            # A failed destroy leaves the plate in the scene forever: it is
            # what a stale "duplicate" mic looks like. Make it loud.
            log.warning("floating mics: could not destroy the mic for "
                        "window.%s: %s", seq, err)
        self._probe_fail_since.pop(seq, None)
        if seq == self._take_seq:
            self._stop_take()

    def _destroy_all(self):
        for seq in list(self._mics):
            self._remove_mic(seq)

    # -- positioning: the shim three-corner probe --------------------------

    def _warn_position_once(self, mic, err):
        if self._pos_warned.get(mic.seq) == type(err).__name__:
            return
        self._pos_warned[mic.seq] = type(err).__name__
        log.warning("floating mics: could not probe window.%s to place its "
                    "mic: %s", mic.seq, err)
        log.debug("floating mics: probe failure traceback", exc_info=True)

    def _position(self, now, dt):
        for mic in tuple(self._mics.values()):
            try:
                scale = self.api.get_mouse_scale(mic.win_handle)
                mx, my = scale if scale else (1920.0, 1080.0)
                ll = self.api.coords(mic.win_handle, 0.0, 0.0)
                lr = self.api.coords(mic.win_handle, float(mx), 0.0)
                ul = self.api.coords(mic.win_handle, 0.0, float(my))
                if not (ll and lr and ul):
                    raise RuntimeError("coordinate probe returned nothing")
                quad = quad_from_probe(ll, lr, ul)
                if quad is None:
                    raise RuntimeError("quad too small to place a mic")
                width, height, right_u, up_u, facing = quad
                # coords() returns a 12-float matrix; the translation lives
                # at m[3], m[7], m[11]. Indexing m[0..2] reads the rotation
                # row, which never moves - the mic then parks at a bogus pose
                # and never tracks the window (live bug 2026-11-09).
                center = [ll[3 + 4 * i]
                          + (lr[3 + 4 * i] - ll[3 + 4 * i]) / 2
                          + (ul[3 + 4 * i] - ll[3 + 4 * i]) / 2
                          for i in range(3)]
                target = corner_target(center, width, height, right_u, up_u,
                                       facing)
                if mic.pose is None:
                    mic.pose = tuple(target)
                else:
                    mic.pose = ease(mic.pose, target, dt or 0.0, SMOOTH_S)
                self.api.set_absolute(
                    mic.handle, STANDING,
                    absolute_matrix(mic.pose, right_u, up_u, facing))
                self._probe_fail_since.pop(mic.seq, None)
                if mic.hidden:
                    self.api.show(mic.handle)
                    mic.hidden = False
            except Exception as err:
                self._warn_position_once(mic, err)
                # The window moved/closed or the runtime briefly refuses the
                # probe. A plate left visible at the old pose reads as a
                # duplicate mic, so hide it once it has been unplaceable for a
                # moment (a transient one-frame failure must not flicker).
                since = self._probe_fail_since.setdefault(mic.seq, now)
                if not mic.hidden and now - since >= HIDE_AFTER_S:
                    try:
                        self.api.hide(mic.handle)
                        mic.hidden = True
                    except Exception as hide_err:
                        log.warning("floating mics: could not hide the mic "
                                    "for window.%s: %s", mic.seq, hide_err)

    # -- input events ------------------------------------------------------

    def _poll_events(self, now, dt):
        for mic in tuple(self._mics.values()):
            while True:
                ev = self.api.poll(mic.handle)
                if ev is None:
                    break
                self._handle_event(mic, ev, now)

    def _handle_event(self, mic, ev, now):
        kind = ev.get("type")
        if kind == MOUSE_MOVE:
            mic.last_move = now
        elif kind == MOUSE_DOWN:
            # Any press on our single-button overlay is the click. Gating on a
            # button mask used to drop presses whenever the event field was
            # read from the wrong offset (see _VREvent); log the raw value so
            # a future layout regression is visible in the journal.
            log.info("window mic: press window.%s button=%s",
                     mic.seq, ev.get("button"))
            self._on_mouse_down(mic.seq, now)
        elif kind == MOUSE_UP:
            log.info("window mic: release window.%s button=%s",
                     mic.seq, ev.get("button"))
            self._on_mouse_up(mic.seq, now)

    # -- state machine -----------------------------------------------------

    def _emit(self, cmd, seq):
        try:
            self.on_command(cmd, seq)
        except Exception:
            pass

    def _start_take(self, seq, now):
        self._take_seq = seq
        self._take_since = now
        self._pressed_at = now
        self._emit("talk_start", seq)

    def _stop_take(self, seq=None):
        seq = seq if seq is not None else self._take_seq
        self._take_seq = None
        self._pressed_at = None
        if seq is not None:
            self._emit("talk_stop", seq)

    def _on_mouse_down(self, seq, now):
        try:
            allowed = self.can_act() and self.enabled
        except Exception:
            allowed = False
        if not allowed:
            log.info("window mic: press window.%s ignored (feature off or "
                     "engine busy/paused)", seq)
            return
        if self._take_seq is not None:
            active = self._take_seq
            self._stop_take(active)
            if active == seq:
                return          # tap-to-toggle: restarting on the same mic
        self._start_take(seq, now)

    def _on_mouse_up(self, seq, now):
        if self._take_seq is None or self._pressed_at is None:
            self._pressed_at = None
            return
        duration = now - self._pressed_at
        self._pressed_at = None
        if self._take_seq == seq and duration >= CLICK_S:
            self._stop_take(seq)

    def _enforce_cap(self, now, dt):
        if self._take_seq is not None and now - self._take_since > MAX_TAKE_S:
            self._stop_take()

    # -- rendering ---------------------------------------------------------

    def _kind_for(self, seq):
        if self._take_seq == seq:
            return "recording"
        try:
            if self.engine_state() in ("working", "error"):
                return "busy"
        except Exception:
            pass
        return "idle"

    def _repaint(self, now, dt):
        for mic in tuple(self._mics.values()):
            hover = now - mic.last_move < HOVER_TTL
            kind = self._kind_for(mic.seq)
            phase = int(now / PULSE_S) % 2 if kind == "recording" else 0
            face = (kind, bool(hover), phase)
            if face == mic.face:
                continue
            mic.face = face
            self._upload(mic)

    def _upload(self, mic):
        kind, hover, phase = mic.face or ("idle", False, 0)
        try:
            rgba = render_mic(kind, hover, phase, TEX_W, TEX_H)
        except Exception as err:
            log.warning("floating mics: texture draw for %s failed: %s",
                        mic.key, err)
            return
        try:
            self.api.set_raw(mic.handle, rgba, TEX_W, TEX_H)
        except Exception as err:
            log.warning("floating mics: texture upload for %s failed: %s",
                        mic.key, err)


# --------------------------------------------------------------------------
# Procedural texture (no Qt): handheld mic on a plate, RGBA bytes
# --------------------------------------------------------------------------

_PLATE = {"idle": (44, 52, 66), "hover": (58, 68, 86),
          "recording": (220, 46, 64), "busy": (214, 152, 48)}
_GLYPH = {"idle": (240, 244, 250), "hover": (240, 244, 250),
          "recording": (255, 255, 255), "busy": (250, 250, 250)}
# Bright rim so the dark plate reads against a dim room (the 1.4.2 lesson:
# a near-black plate is invisible; the old pill was bright orange).
_RIM = {"idle": (46, 230, 184), "hover": (46, 230, 184),
        "recording": (255, 255, 255), "busy": (255, 210, 120)}


def render_mic(kind, hover, phase, w, h):
    """RGBA bytearray for a mic face: idle / hover / recording (pulse) / busy.

    Filled handheld mic (ball grille + tapered body + cable stub) tilted on a
    -30 degree diagonal, occupying ~46% of the texture width — the 1.4.2
    lesson: thin strokes collapse to a blob at panel size, fills survive.
    """
    plate = _PLATE.get(kind, _PLATE["idle"])
    glyph = _GLYPH.get(kind, _GLYPH["idle"])
    rim = _RIM.get(kind, _RIM["idle"])
    ring = (255, 255, 255) if hover else rim

    cx, cy = w / 2.0, h / 2.0
    # Plate: filled ellipse with a 2 px transparent margin.
    prx, pry = w / 2.0 - 3.0, h / 2.0 - 3.0
    # Ring: annulus just inside the plate edge.
    rrx, rry = prx - 4.0, pry - 4.0
    # Glyph geometry in a -30 degree rotated frame, centred on the plate.
    angle = math.radians(-30.0)
    ca, sa = math.cos(angle), -math.sin(angle)

    buf = bytearray(w * h * 4)
    for py in range(h):
        dy = py - cy
        for px in range(w):
            dx = px - cx
            # Plate / ring coverage (elliptical distance).
            d_plate = (dx / prx) ** 2 + (dy / pry) ** 2
            if d_plate > 1.0:
                continue
            # Glyph coordinates in the rotated frame.
            gx = dx * ca - dy * sa
            gy = dx * sa + dy * ca
            inside = False
            # Ball grille: filled circle centred (0, -13), r 15.
            if gx * gx + (gy + 13.0) ** 2 <= 15.0 ** 2:
                inside = True
            # Tapered body: trapezoid y in [-2, 24], half-width 11 -> 6.
            elif -2.0 <= gy <= 24.0:
                half = 11.0 + (gy + 2.0) * (6.0 - 11.0) / 26.0
                if abs(gx) <= half:
                    inside = True
            # Cable stub: thin line below the body.
            elif 24.0 < gy <= 30.0 and abs(gx) <= 1.6:
                inside = True
            colour = glyph if inside else plate
            alpha = 255
            if not inside:
                # Ring on hover / recording pulse: annulus near the rim.
                d_ring = (dx / rrx) ** 2 + (dy / rry) ** 2
                if (hover or kind == "recording") and 0.82 <= d_ring <= 1.0:
                    colour = ring
                    if kind == "recording" and phase == 1:
                        alpha = 175
            i = (py * w + px) * 4
            buf[i] = colour[0]
            buf[i + 1] = colour[1]
            buf[i + 2] = colour[2]
            buf[i + 3] = alpha
    return buf

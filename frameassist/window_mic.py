"""Optional mic button parked on each floating (gamescope) window.

Off by default (`window_mic` in the config). When on, a small mic plate sits at
the bottom-right of every floating window; hold it with the laser to dictate
into that window, release to type there.

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
PULSE_S = 0.25      # one recording-ring animation phase
HOVER_TTL = 0.5     # a mouse-move keeps a mic highlighted this long
MAX_TAKE_S = 300.0  # safety cap: force-stop a take left open this long
ALPHA = 0.9
SORT_ORDER = 80

# Plate geometry. Texture is 5:3, so the plate is 5 cm by 3 cm.
PLATE_METERS = 0.05
RIGHT_INSET = PLATE_METERS / 2 + 0.02
BELOW = 0.03
# Along the facing axis, which points at the viewer on these windows.
TOWARD_VIEWER = 0.005

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

    Anchored at the bottom-right: inset from the right edge, below the bottom
    edge, nudged toward the viewer.
    """
    fx, fy = factor
    inset = min(RIGHT_INSET, width * fx * 0.35)
    return tuple(
        center[row]
        + right_u[row] * (width * fx / 2 - inset)
        - up_u[row] * (height * fy / 2 + BELOW)
        + facing[row] * TOWARD_VIEWER
        for row in range(3)
    )


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
    """The front of VREvent_t: type, device, age, then the data union.

    Layout matches the runtime: eventType @0, trackedDeviceIndex @4,
    eventAgeSeconds @8, data @12. VREvent_Mouse puts button @12, x @16, y @20;
    VREvent_Overlay puts the handle @12 and memoryBlockId @20.
    """

    _fields_ = [
        ("eventType", ctypes.c_uint32),
        ("trackedDeviceIndex", ctypes.c_uint32),
        ("eventAgeSeconds", ctypes.c_float),
        ("overlayHandle", ctypes.c_uint64),
        ("memoryBlockId", ctypes.c_uint32),
        ("mouseButton", ctypes.c_uint32),
        ("mouseX", ctypes.c_float),
        ("mouseY", ctypes.c_float),
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

        self.glue = ctypes.CDLL(str(shim_path or SHIM))
        self.glue.frameassist_overlay_point.argtypes = [
            ctypes.c_void_p, u64, i32, f32, f32,
            ctypes.POINTER(_Mat)]
        self.glue.frameassist_overlay_point.restype = i32
        self._coords_fn = fns[FN["coords"]]

    def close(self):
        if getattr(self, "_inited", False):
            try:
                shutdown = ctypes.CFUNCTYPE(None)(self.lib.VR_ShutdownInternal)
                shutdown()
            except Exception:
                pass
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
        self._destroy(ctypes.c_uint64(handle))

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
            "x": float(ev.mouseX),
            "y": float(ev.mouseY),
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
                 "pose")

    def __init__(self, seq, key, handle, win_handle):
        self.seq = seq
        self.key = key
        self.handle = handle
        self.win_handle = win_handle
        self.face = None        # last (kind, hover, phase) painted
        self.last_move = 0.0
        self.pose = None        # last eased centre (3-tuple)


class WindowMic:
    """Creates, positions and drives one mic overlay per floating window.

    ``api`` is any object with the OverlayApi surface (a fake in tests).
    ``on_command(cmd, seq)`` receives "talk_start"/"talk_stop".
    ``can_act()`` lets the owner refuse new takes; ``engine_state()``
    feeds the busy face.
    """

    def __init__(self, api, app_key, on_command=None, can_act=None,
                 engine_state=None, clock=time.monotonic, enabled=True):
        self.api = api
        self.app_key = app_key
        self.on_command = on_command or (lambda cmd, seq: None)
        self.can_act = can_act or (lambda: True)
        self.engine_state = engine_state or (lambda: None)
        self.clock = clock
        self.enabled = bool(enabled)
        self._mics = {}            # seq -> _Mic
        self._last_scan = 0.0
        self._seen_logged = None   # last window set we logged
        self._pos_warned = {}      # seq -> exception type already warned
        self._take_seq = None
        self._take_since = 0.0
        self._pressed_at = None
        self._last_tick = None

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
        for step in (self._maybe_scan, self._poll_events, self._position,
                     self._repaint, self._enforce_cap):
            try:
                step(now, dt)
            except Exception as err:
                log.warning("floating mics: %s failed: %s",
                            getattr(step, "__name__", step), err)

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
        windows = self._scan_windows()
        seen = set(windows)
        if seen != self._seen_logged:
            log.info("floating mics: gamescope windows: %s",
                     sorted(seen) or "none")
            self._seen_logged = set(seen)

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
        except Exception:
            pass
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
                    continue
                width, height, right_u, up_u, facing = quad
                center = [ll[i] + (lr[i] - ll[i]) / 2 + (ul[i] - ll[i]) / 2
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
            except Exception as err:
                self._warn_position_once(mic, err)

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
            if ev.get("button", 0) & LEFT_BUTTON:
                self._on_mouse_down(mic.seq, now)
        elif kind == MOUSE_UP:
            if ev.get("button", 0) & LEFT_BUTTON:
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

_PLATE = {"idle": (35, 40, 51), "hover": (48, 55, 68),
          "recording": (197, 40, 58), "busy": (179, 128, 42)}
_GLYPH = {"idle": (238, 241, 246), "hover": (238, 241, 246),
          "recording": (255, 255, 255), "busy": (238, 241, 246)}


def render_mic(kind, hover, phase, w, h):
    """RGBA bytearray for a mic face: idle / hover / recording (pulse) / busy.

    Filled handheld mic (ball grille + tapered body + cable stub) tilted on a
    -30 degree diagonal, occupying ~46% of the texture width — the 1.4.2
    lesson: thin strokes collapse to a blob at panel size, fills survive.
    """
    plate = _PLATE.get(kind, _PLATE["idle"])
    glyph = _GLYPH.get(kind, _GLYPH["idle"])
    ring = (255, 255, 255) if hover else (255, 151, 161)

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

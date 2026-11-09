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
import math
import os
import threading
import time
from pathlib import Path

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

# VREvent_t is 64 bytes with this runtime; the fields we use are at the front.
EVENT_SIZE = 64
MOUSE_MOVE = 300
MOUSE_DOWN = 301
MOUSE_UP = 302
FOCUS_ENTER = 303
FOCUS_LEAVE = 304
LEFT_BUTTON = 1

# VROverlayFlags bits. Without these the plate is invisible in the dashboard.
VISIBLE_IN_DASHBOARD = 1 << 15
SORT_WITH_NON_SCENE = 1 << 14

# A press shorter than this latches recording on; a longer one is hold-to-talk.
CLICK_S = 0.30

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

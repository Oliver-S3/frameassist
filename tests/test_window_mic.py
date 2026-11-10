"""Tests for the optional per-window mic button.

Only pure helpers and the event struct layout are covered here. Nothing in this
file needs a headset or a live SteamVR runtime.
"""

import ctypes
import math

from frameassist import window_mic as wm


# --- availability ---------------------------------------------------------

def test_unavailable_reason_reports_missing_shim(monkeypatch, tmp_path):
    monkeypatch.setattr(wm, "OPENVR_LIB", tmp_path / "libopenvr_api.so")
    (tmp_path / "libopenvr_api.so").write_bytes(b"x")
    monkeypatch.setattr(wm, "SHIM", tmp_path / "overlay_point.so")
    reason = wm.unavailable_reason()
    assert reason is not None
    assert "overlay_point.so" in reason


def test_unavailable_reason_reports_missing_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(wm, "OPENVR_LIB", tmp_path / "libopenvr_api.so")
    monkeypatch.setattr(wm, "SHIM", tmp_path / "overlay_point.so")
    reason = wm.unavailable_reason()
    assert reason is not None
    assert "SteamVR" in reason


def test_available_when_both_present(monkeypatch, tmp_path):
    (tmp_path / "libopenvr_api.so").write_bytes(b"x")
    (tmp_path / "overlay_point.so").write_bytes(b"x")
    monkeypatch.setattr(wm, "OPENVR_LIB", tmp_path / "libopenvr_api.so")
    monkeypatch.setattr(wm, "SHIM", tmp_path / "overlay_point.so")
    assert wm.available() is True


# --- quat_basis -----------------------------------------------------------

def test_quat_basis_identity_is_world_axes():
    right, up, facing = wm.quat_basis(0.0, 0.0, 0.0, 1.0)
    assert right == (1.0, 0.0, 0.0)
    assert up == (0.0, 1.0, 0.0)
    assert facing == (0.0, 0.0, 1.0)


def test_quat_basis_axes_are_unit_length():
    qx, qy, qz, qw = 0.3, -0.2, 0.5, 0.78
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm
    for axis in wm.quat_basis(qx, qy, qz, qw):
        assert abs(math.sqrt(sum(c * c for c in axis)) - 1.0) < 1e-6


def test_quat_basis_180_about_up_flips_right_and_facing():
    right, up, facing = wm.quat_basis(0.0, 1.0, 0.0, 0.0)
    assert right == (-1.0, 0.0, 0.0)
    assert up == (0.0, 1.0, 0.0)
    # A half turn about up leaves facing pointing the other way.
    assert facing == (0.0, 0.0, -1.0)


# --- ease -----------------------------------------------------------------

def test_ease_snaps_past_threshold():
    current = (0.0, 0.0, 0.0)
    target = (1.0, 0.0, 0.0)
    assert wm.ease(current, target, 0.016, wm.SMOOTH_S) == target


def test_ease_approaches_without_snapping():
    current = (0.0, 0.0, 0.0)
    target = (0.2, 0.0, 0.0)
    got = wm.ease(current, target, 0.016, wm.SMOOTH_S)
    assert 0.0 < got[0] < 0.2


def test_ease_with_zero_dt_jumps_to_target():
    current = (0.0, 0.0, 0.0)
    target = (0.1, 0.0, 0.0)
    assert wm.ease(current, target, 0.0, wm.SMOOTH_S) == target


# --- resize_factor --------------------------------------------------------

def test_resize_factor_divides_by_z():
    assert wm.resize_factor(0.369, 0.369, 0.369) == (1.0, 1.0)


def test_resize_factor_tracks_growth():
    fx, fy = wm.resize_factor(0.5, 0.4, 0.25)
    assert abs(fx - 2.0) < 1e-9
    assert abs(fy - 1.6) < 1e-9


def test_resize_factor_rejects_degenerate_z():
    assert wm.resize_factor(0.4, 0.4, 0.0) is None
    assert wm.resize_factor(0.4, 0.4, 0.01) is None
    assert wm.resize_factor(0.4, 0.4, None) is None
    assert wm.resize_factor(None, 0.4, 0.4) is None


# --- quad_from_probe ------------------------------------------------------

def _mat(x, y, z):
    """A 12-float matrix whose translation is (x, y, z), axis-aligned."""
    m = [1.0, 0.0, 0.0, x,
         0.0, 1.0, 0.0, y,
         0.0, 0.0, 1.0, z]
    return m


def test_quad_from_probe_measures_a_window():
    lower_left = _mat(0.0, 0.0, -1.0)
    lower_right = _mat(1.0, 0.0, -1.0)
    upper_left = _mat(0.0, 1.0, -1.0)
    quad = wm.quad_from_probe(lower_left, lower_right, upper_left)
    assert quad is not None
    width, height, right_u, up_u, facing = quad
    assert abs(width - 1.0) < 1e-9
    assert abs(height - 1.0) < 1e-9
    assert right_u == (1.0, 0.0, 0.0)
    assert up_u == (0.0, 1.0, 0.0)
    assert facing == (0.0, 0.0, 1.0)


def test_quad_from_probe_rejects_a_tiny_quad():
    lower_left = _mat(0.0, 0.0, -1.0)
    lower_right = _mat(0.01, 0.0, -1.0)
    upper_left = _mat(0.0, 0.01, -1.0)
    assert wm.quad_from_probe(lower_left, lower_right, upper_left) is None


# --- corner_target / absolute_matrix -------------------------------------

def test_corner_target_hangs_outside_the_right_edge():
    """Live 2026-11-10: the plate moved fully outside the window so it does
    not sit beside the grab bar SteamVR draws under a floating window."""
    center = (0.0, 1.5, -1.0)
    right_u, up_u, facing = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    got = wm.corner_target(center, 1.0, 0.8, right_u, up_u, facing)
    # The plate's near edge clears the window's right edge by a real gap.
    assert abs((got[0] - wm.PLATE_METERS / 2)
               - (center[0] + 1.0 / 2 + wm.PLATE_OUT_GAP)) < 1e-9
    # It hangs below the bottom edge by a fixed drop.
    assert abs(got[1] - (center[1] - 0.8 / 2 - wm.PLATE_DROP)) < 1e-9
    # And floats in front of the (curved) window plane.
    assert abs(got[2] - (center[2] + wm.PLATE_FRONT)) < 1e-9


def test_corner_target_gap_does_not_scale_with_the_window():
    center = (0.0, 0.0, 0.0)
    right_u, up_u, facing = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    got = wm.corner_target(center, 0.1, 0.1, right_u, up_u, facing)
    assert abs((got[0] - wm.PLATE_METERS / 2)
               - (0.1 / 2 + wm.PLATE_OUT_GAP)) < 1e-9


# --- VREvent_t layout (live 2026-11-10 regression) ------------------------

def test_vrevent_mouse_button_is_read_from_the_aligned_union():
    """The data union is 8-byte aligned (@16), so VREvent_Mouse_t.button is at
    @24 - not in a sequentially modelled @28. Reading @28 gave garbage and the
    old ``button & LEFT_BUTTON`` gate then dropped every other press."""
    ev = wm._VREvent
    assert ctypes.sizeof(ev) == 40
    assert ev.eventType.offset == 0
    assert ev.trackedDeviceIndex.offset == 4
    assert ev.eventAgeSeconds.offset == 8
    assert ev.overlayHandle.offset == 16
    assert ev.mouseButton.offset == 24


def test_absolute_matrix_places_translation_in_the_last_column():
    m = wm.absolute_matrix((1.0, 2.0, 3.0),
                           (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    assert len(m) == 12
    assert m[3] == 1.0
    assert m[7] == 2.0
    assert m[11] == 3.0
    assert m[0] == 1.0
    assert m[5] == 1.0
    assert m[10] == 1.0

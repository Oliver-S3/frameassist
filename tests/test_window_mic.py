"""Tests for the optional per-window mic button.

Only the pure geometry/availability helpers are covered here. Nothing in this
file needs a headset or a live SteamVR runtime.
"""

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

def test_corner_target_sits_bottom_right_and_toward_viewer():
    center = (0.0, 1.5, -1.0)
    right_u, up_u, facing = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    got = wm.corner_target(center, 1.0, 0.8, right_u, up_u, facing)
    # Right of the window centre.
    assert got[0] > center[0]
    # Below the window centre.
    assert got[1] < center[1]
    # Nudged toward the viewer.
    assert abs(got[2] - (center[2] + wm.TOWARD_VIEWER)) < 1e-9


def test_corner_target_inset_is_capped_for_small_windows():
    center = (0.0, 0.0, 0.0)
    right_u, up_u, facing = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    # A 0.1 m wide window: inset must not exceed 35% of its width.
    got = wm.corner_target(center, 0.1, 0.1, right_u, up_u, facing)
    assert got[0] <= 0.1 * 0.35


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

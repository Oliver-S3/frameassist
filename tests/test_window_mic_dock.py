"""Tests for pinning the bar mic to the Steam bar's own transform."""

import pytest

from frameassist import window_mic as wm


# The live bar pose read from valve.steam.gamepadui.bar on the Frame
# (2026-11-10): an Absolute overlay with Steam's 0.369 dock scale.
LIVE_BAR = [
    0.1814, -0.1692, 0.2732, -1.4349,
    0.0, 0.3137, 0.1943, 1.3485,
    -0.3214, -0.0955, 0.1542, -0.8475,
]


def test_dock_pose_lands_right_of_the_bar():
    pose = wm._dock_pose(LIVE_BAR, 1.0)
    assert pose is not None
    target, right_u, up_u, facing = pose
    # Normalised basis: the plate parallels the bar, upright, facing the viewer.
    assert right_u == pytest.approx((0.49148, 0.0, -0.87079), abs=5e-4)
    assert up_u[1] > 0.5
    assert facing[2] > 0.0
    # Right edge of the scaled 1 m bar, plus the 5 cm plate and gap.
    assert target == pytest.approx((-1.2926, 1.3590, -1.0652), abs=5e-3)


def test_dock_pose_rejects_a_bar_that_is_down():
    assert wm._dock_pose([0.0] * 12, 1.0) is None
    assert wm._dock_pose(None, 1.0) is None
    assert wm._dock_pose([1e-6, 0, 0, 1.0, 0, 1e-6, 0, 1.0, 0, 0, 1e-6, 1.0],
                         1.0) is None


def test_dock_pose_unit_scale():
    m = [1.0, 0.0, 0.0, 2.0,
         0.0, 1.0, 0.0, 1.0,
         0.0, 0.0, 1.0, -1.0]
    target, right_u, up_u, facing = wm._dock_pose(m, 1.0)
    assert target == pytest.approx((2.0 + 0.5 + 0.075, 1.0, -1.0 + 0.02), abs=1e-6)
    assert right_u == pytest.approx((1.0, 0.0, 0.0), abs=1e-9)
    assert up_u == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
    assert facing == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

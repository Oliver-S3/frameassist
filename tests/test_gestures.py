"""Tests for the gesture engine — the heart of FrameAssist."""

import pytest

from frameassist import gestures as G
from frameassist.gestures import GestureEngine


class Collector:
    def __init__(self):
        self.commands = []

    def __call__(self, cmd):
        self.commands.append(cmd)


def engine(timings=None):
    c = Collector()
    return GestureEngine(on_command=c, timings=timings), c


def R(engine, kind, side, t):
    engine.press(kind, side, t)
    engine.tick(t)


def RE(engine, kind, side, t):
    engine.release(kind, side, t)
    engine.tick(t)


# -- hold both grips to talk --------------------------------------------------
def test_hold_both_grips_starts_and_stops_talk():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    R(eng, G.GRIP, G.RIGHT, 0.05)
    eng.tick(0.10)
    assert c.commands == []
    eng.tick(0.31)  # held beyond talk_hold_min (0.30)
    assert c.commands == [G.TALK_START]
    RE(eng, G.GRIP, G.LEFT, 0.50)
    assert c.commands[-1] == G.TALK_STOP


def test_releasing_one_grip_stops_talk():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    R(eng, G.GRIP, G.RIGHT, 0.05)
    eng.tick(0.35)
    assert G.TALK_START in c.commands
    RE(eng, G.GRIP, G.RIGHT, 0.45)
    assert c.commands[-1] == G.TALK_STOP
    # the remaining grip being released alone is not another tap
    RE(eng, G.GRIP, G.LEFT, 0.5)
    assert G.TALK_STOP in c.commands


def test_short_both_grips_is_a_tap_not_talk():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    R(eng, G.GRIP, G.RIGHT, 0.02)
    RE(eng, G.GRIP, G.RIGHT, 0.10)
    RE(eng, G.GRIP, G.LEFT, 0.12)
    assert c.commands == []


# -- double-tap both grips toggles hands-free ---------------------------------
def test_double_tap_both_starts_handsfree():
    eng, c = engine()
    # tap 1
    R(eng, G.GRIP, G.LEFT, 0.0)
    R(eng, G.GRIP, G.RIGHT, 0.02)
    RE(eng, G.GRIP, G.RIGHT, 0.10)
    RE(eng, G.GRIP, G.LEFT, 0.12)
    # tap 2 within the double window
    R(eng, G.GRIP, G.LEFT, 0.30)
    R(eng, G.GRIP, G.RIGHT, 0.32)
    RE(eng, G.GRIP, G.RIGHT, 0.40)
    RE(eng, G.GRIP, G.LEFT, 0.42)
    assert c.commands == [G.HANDSFREE_START]


def test_single_chord_tap_stops_handsfree():
    eng, c = engine()
    # start hands-free
    for start in (0.0, 0.30):
        R(eng, G.GRIP, G.LEFT, start)
        R(eng, G.GRIP, G.RIGHT, start + 0.02)
        RE(eng, G.GRIP, G.RIGHT, start + 0.10)
        RE(eng, G.GRIP, G.LEFT, start + 0.12)
    assert c.commands[-1] == G.HANDSFREE_START
    assert eng.handsfree
    # a single chord tap stops it
    R(eng, G.GRIP, G.LEFT, 1.0)
    R(eng, G.GRIP, G.RIGHT, 1.02)
    RE(eng, G.GRIP, G.RIGHT, 1.10)
    RE(eng, G.GRIP, G.LEFT, 1.12)
    assert c.commands[-1] == G.HANDSFREE_STOP
    assert not eng.handsfree


def test_handsfree_auto_stops_after_max():
    eng, c = engine(timings={"handsfree_max": 10.0})
    for start in (0.0, 0.30):
        R(eng, G.GRIP, G.LEFT, start)
        R(eng, G.GRIP, G.RIGHT, start + 0.02)
        RE(eng, G.GRIP, G.RIGHT, start + 0.10)
        RE(eng, G.GRIP, G.LEFT, start + 0.12)
    assert eng.handsfree
    eng.tick(5.0)
    assert eng.handsfree
    eng.tick(11.0)
    assert not eng.handsfree
    assert c.commands[-1] == G.TALK_STOP


def test_handsfree_second_tap_within_window_is_start_stop():
    eng, c = engine()
    for start in (0.0, 0.30):
        R(eng, G.GRIP, G.LEFT, start)
        R(eng, G.GRIP, G.RIGHT, start + 0.02)
        RE(eng, G.GRIP, G.RIGHT, start + 0.10)
        RE(eng, G.GRIP, G.LEFT, start + 0.12)
    assert eng.handsfree
    # user holds grips while hands-free: that's just talking, no toggle
    R(eng, G.GRIP, G.LEFT, 1.0)
    R(eng, G.GRIP, G.RIGHT, 1.02)
    eng.tick(1.35)
    assert not eng._talking_hold  # hands-free already covers talking


# -- single-grip double taps: copy / paste ------------------------------------
def test_double_tap_left_copies():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    RE(eng, G.GRIP, G.LEFT, 0.10)
    R(eng, G.GRIP, G.LEFT, 0.30)
    RE(eng, G.GRIP, G.LEFT, 0.40)
    assert c.commands == [G.COPY]


def test_double_tap_right_pastes():
    eng, c = engine()
    R(eng, G.GRIP, G.RIGHT, 0.0)
    RE(eng, G.GRIP, G.RIGHT, 0.10)
    R(eng, G.GRIP, G.RIGHT, 0.30)
    RE(eng, G.GRIP, G.RIGHT, 0.40)
    assert c.commands == [G.PASTE]


def test_taps_on_different_sides_do_not_double():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    RE(eng, G.GRIP, G.LEFT, 0.10)
    R(eng, G.GRIP, G.RIGHT, 0.30)
    RE(eng, G.GRIP, G.RIGHT, 0.40)
    assert c.commands == []


def test_single_tap_alone_does_nothing():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    RE(eng, G.GRIP, G.LEFT, 0.10)
    assert c.commands == []


def test_double_tap_window_expires():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    RE(eng, G.GRIP, G.LEFT, 0.10)
    R(eng, G.GRIP, G.LEFT, 0.60)  # beyond double_ms (0.45)
    RE(eng, G.GRIP, G.LEFT, 0.70)
    assert c.commands == []


def test_hold_clears_tap_windows():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    RE(eng, G.GRIP, G.LEFT, 0.10)
    # a full both-grip hold
    R(eng, G.GRIP, G.LEFT, 0.30)
    R(eng, G.GRIP, G.RIGHT, 0.32)
    RE(eng, G.GRIP, G.RIGHT, 0.40)   # < tap_max but chord broke early
    RE(eng, G.GRIP, G.LEFT, 0.42)
    # left tap again: should NOT pair with the old tap
    R(eng, G.GRIP, G.LEFT, 0.60)
    RE(eng, G.GRIP, G.LEFT, 0.70)
    assert c.commands == []


# -- stick flicks -------------------------------------------------------------
def test_flick_right_sends():
    eng, c = engine()
    R(eng, G.STICK, G.RIGHT, 0.0)
    RE(eng, G.STICK, G.RIGHT, 0.10)
    assert c.commands == [G.ENTER]


def test_flick_left_escapes():
    eng, c = engine()
    R(eng, G.STICK, G.LEFT, 0.0)
    RE(eng, G.STICK, G.LEFT, 0.10)
    assert c.commands == [G.ESCAPE]


def test_holding_stick_is_not_a_flick():
    eng, c = engine()
    R(eng, G.STICK, G.RIGHT, 0.0)
    RE(eng, G.STICK, G.RIGHT, 0.60)
    assert c.commands == []


def test_stick_flick_does_not_count_as_grip_tap():
    eng, c = engine()
    R(eng, G.STICK, G.RIGHT, 0.0)
    RE(eng, G.STICK, G.RIGHT, 0.10)
    R(eng, G.GRIP, G.LEFT, 0.30)
    RE(eng, G.GRIP, G.LEFT, 0.40)
    assert c.commands == [G.ENTER]


# -- edge cases ---------------------------------------------------------------
def test_skewed_grip_presses_still_chord():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    eng.tick(0.10)
    R(eng, G.GRIP, G.RIGHT, 0.12)  # within chord_skew of the first? (0.15)
    eng.tick(0.31)
    assert c.commands == [G.TALK_START]


def test_asymmetric_talk_stop_does_not_spawn_copy():
    eng, c = engine()
    R(eng, G.GRIP, G.LEFT, 0.0)
    R(eng, G.GRIP, G.RIGHT, 0.05)
    eng.tick(0.35)
    c.commands.clear()
    RE(eng, G.GRIP, G.RIGHT, 0.4)
    RE(eng, G.GRIP, G.LEFT, 0.41)
    assert c.commands == [G.TALK_STOP]

"""Tests for the read-only vrserver web-socket button reader.

This is the mechanism that lets grips work in the Frame's desktop/UI, where
SteamVR Input won't route overlay-global buttons without a game running.
"""

import json

import pytest

from frameassist import vrws as V
from frameassist import steamvr as S


@pytest.fixture
def vr(tmp_path):
    """A working fake-openvr session (same as test_steamvr.vr)."""
    import pathlib

    import frameassist.steamvr as svr_mod
    svr_mod.DATA_DIR = pathlib.Path(tmp_path / "data")
    svr_mod.DIAG_PATH = pathlib.Path(tmp_path / "state" / "steamvr.json")
    svr_mod.DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)

    from .fake_openvr import BUTTON_STATES, make_openvr
    BUTTON_STATES.clear()
    return make_openvr()


def test_apply_message_maps_grip_and_stick_per_side():
    devices = {"/user/hand/left": "left", "/user/hand/right": "right"}
    state = {}
    V.apply_message(json.dumps({
        "type": "update_component_states",
        "device": "/user/hand/left",
        "components": {"/input/grip/click": True, "/input/thumbstick/click": False},
    }), devices, state)
    assert state == {"grip_l": True, "stick_l": False}
    V.apply_message(json.dumps({
        "type": "update_component_states",
        "device": "/user/hand/right",
        "components": {"/input/grip/click": True, "/input/thumbstick/click": True},
    }), devices, state)
    assert state == {"grip_l": True, "stick_l": False, "grip_r": True, "stick_r": True}


def test_apply_message_ignores_unknown_device_and_component():
    devices = {"/user/hand/right": "right"}
    state = {"grip_r": True}
    V.apply_message(json.dumps({
        "type": "update_component_states",
        "device": "/user/head",
        "components": {"/input/system/click": True},
    }), devices, state)
    assert state == {"grip_r": True}  # untouched
    V.apply_message("not json {", devices, state)
    assert state == {"grip_r": True}


def test_controllers_parses_sides():
    import io
    payload = json.dumps({"devices": [
        {"controller_type": "frame_hmd", "root_path": "/user/head"},
        {"controller_type": "frame_controller", "root_path": "/user/hand/left",
         "side": "left"},
        {"controller_type": "frame_controller", "root_path": "/user/hand/right",
         "side": "right"},
        {"controller_type": "frame_controller", "root_path": "/user/hand/left",
         "side": ""},  # no side -> skipped
    ]}).encode()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    orig = V.urllib.request.urlopen
    try:
        V.urllib.request.urlopen = lambda req, timeout=None: Resp(payload)
        assert V.controllers() == [("/user/hand/left", "left"), ("/user/hand/right", "right")]
    finally:
        V.urllib.request.urlopen = orig


def test_step_merges_reader_buttons(vr):
    """When the web-socket reader reports buttons, step feeds them to the
    gesture engine as if SteamVR Input had delivered them."""
    from frameassist.gestures import GestureEngine

    class Clock:
        def __init__(self):
            self.t = 100.0

        def __call__(self):
            return self.t

        def advance(self):
            self.t += 1 / 60

    class FakeReader:
        connected = True

        def __init__(self, buttons):
            self._buttons = buttons

        def buttons(self):
            return set(self._buttons)

    clock = Clock()
    svr = S.SteamVR(clock=clock)
    got = []
    svr.set_gesture_engine(GestureEngine(lambda cmd: got.append(cmd)))
    svr.open(vr)
    svr.reader = FakeReader(["grip_l", "grip_r"])

    # let the chord accumulate past talk_hold_min
    for _ in range(40):
        svr.step()
        clock.advance()
    assert "talk_start" in got

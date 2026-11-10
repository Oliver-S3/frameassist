"""Tests for the window-mic wiring: SteamVR sync, service plumbing, UI toggle."""

import pytest

from frameassist import steamvr as svr_mod
from frameassist.steamvr import SteamVR

from .fake_openvr import make_openvr


class Clock:
    def __init__(self, start=100.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, step=1 / 60):
        self.t += step


class RecordingMic:
    """Stands in for WindowMic; records lifecycle calls."""

    def __init__(self, api=None, app_key=None, on_command=None, **kwargs):
        self.on_command = on_command
        self.ticks = 0
        self.destroyed = False

    def tick(self, now):
        self.ticks += 1

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def vr(tmp_path, monkeypatch):
    import pathlib
    svr_mod.DATA_DIR = pathlib.Path(tmp_path / "data")
    svr_mod.DIAG_PATH = pathlib.Path(tmp_path / "state" / "steamvr.json")
    svr_mod.DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUTTONS_OPENVR = make_openvr()
    return BUTTONS_OPENVR


def _make(clock, window_mic=False):
    svr = SteamVR(clock=clock, window_mic=window_mic)
    svr.set_gesture_engine.__self__  # exists
    return svr


def test_window_mic_off_by_default():
    svr = SteamVR()
    assert svr.window_mic is False
    assert svr.mic is None


def test_sync_mic_starts_when_enabled_and_session_open(vr, monkeypatch):
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=True)
    svr.vr = vr  # session "open"
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", RecordingMic)
    svr._sync_mic()
    assert svr.mic is not None
    assert isinstance(svr.mic, RecordingMic)


def test_sync_mic_skips_when_unavailable(vr, monkeypatch):
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=True)
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason",
                        lambda: "overlay_point.so was not built")
    svr._sync_mic()
    assert svr.mic is None


def test_sync_mic_waits_for_session(vr):
    svr = SteamVR(clock=Clock(), window_mic=True)
    svr.vr = None
    svr._sync_mic()
    assert svr.mic is None


def test_set_options_toggles_mic(vr, monkeypatch):
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=False)
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", RecordingMic)
    svr.set_options(window_mic=True)
    assert svr.mic is not None
    svr.set_options(window_mic=False)
    assert svr.mic is None


def test_step_ticks_the_mic(vr, monkeypatch):
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=True)
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", RecordingMic)
    svr._sync_mic()
    mic = svr.mic
    svr.open(vr)          # full session setup, as the other steamvr tests do
    svr._sync_mic()       # open() replaced vr state; re-sync against it
    mic = svr.mic
    svr.step()
    assert mic.ticks == 1


def test_mic_can_act_refuses_when_held_or_busy(vr):
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=True)
    assert svr._mic_can_act() is False  # no engine
    svr.engine = object()
    svr.hold = True
    assert svr._mic_can_act() is False
    svr.hold = False
    svr.paused = False
    svr.engine_state = "working"
    assert svr._mic_can_act() is False
    svr.engine_state = "error"
    assert svr._mic_can_act() is False
    svr.engine_state = "listening"
    assert svr._mic_can_act() is True


def test_mic_talk_commands_reach_the_engine(vr, monkeypatch):
    """Regression: the mic's on_command called SteamVR.send_command, which did
    not exist — clicks were swallowed and nothing recorded (live 2026-11-09)."""
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=True)
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", RecordingMic)
    svr._sync_mic()
    assert svr.mic is not None

    commands = []

    class Engine:
        def on_command(self, cmd):
            commands.append(cmd)

    svr.set_gesture_engine(Engine())
    svr.mic.on_command("talk_start", 49)
    svr.mic.on_command("talk_stop", 49)
    assert commands == ["talk_start", "talk_stop"]


def test_mic_command_without_an_engine_is_safe():
    svr = SteamVR(window_mic=False)
    svr.send_command("talk_start")  # must not raise
    assert svr.mic is None


def test_show_state_feeds_engine_state(vr):
    clock = Clock()
    svr = SteamVR(clock=clock)
    svr.show_state("working", "decoding")
    assert svr.engine_state == "working"
    svr.show_state("listening", "")
    assert svr.engine_state == "listening"

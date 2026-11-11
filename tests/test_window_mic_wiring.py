"""Tests for the window-mic wiring: SteamVR sync, service plumbing, UI toggle."""

import threading
import time

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


class HudRecordingMic(RecordingMic):
    def __init__(self, api=None, app_key=None, on_command=None, hud=None,
                 **kwargs):
        super().__init__(api, app_key, on_command, **kwargs)
        self.hud = hud


def test_hud_anchor_pins_the_mic_to_the_head(vr, monkeypatch):
    svr = SteamVR(clock=Clock(), window_mic=True, window_mic_anchor="hud")
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", HudRecordingMic)
    svr._sync_mic()
    mic = svr.mic
    assert mic.hud is not None
    mic.hud("frameassist.voicetyping.mic.hud")
    (handle, device, matrix), = vr.VROverlay().transforms
    assert handle == "frameassist.voicetyping.mic.hud"
    assert device == vr.k_unTrackedDeviceIndex_Hmd
    assert matrix.m[2][3] < 0  # in front of the face
    assert matrix.m[0][3] > 0  # to the right, beside the toolbar


def test_window_anchor_leaves_the_mic_head_unpinned(vr, monkeypatch):
    svr = SteamVR(clock=Clock(), window_mic=True, window_mic_anchor="window")
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", HudRecordingMic)
    svr._sync_mic()
    assert svr.mic.hud is None


class AnchorRecordingMic(RecordingMic):
    def __init__(self, api=None, app_key=None, on_command=None, hud=None,
                 anchor=None, **kwargs):
        super().__init__(api, app_key, on_command, **kwargs)
        self.hud = hud
        self.anchor = anchor


def test_changing_anchor_rebuilds_the_mic(vr, monkeypatch):
    """The crash path: Settings changed the Mic-button anchor, _stop_mic ran
    unlocked against the VR tick and SEGV'd (live 2026-11-10 16:20:19)."""
    svr = SteamVR(clock=Clock(), window_mic=True, window_mic_anchor="bar")
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", AnchorRecordingMic)
    svr._sync_mic()
    old = svr.mic
    assert old.anchor == "bar"
    svr.set_options(window_mic_anchor="window")
    assert old.destroyed
    assert svr.mic is not old
    assert svr.mic.anchor == "window"


class BlockingMic(RecordingMic):
    def __init__(self, api=None, app_key=None, on_command=None, **kwargs):
        super().__init__(api, app_key, on_command, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def tick(self, now):
        self.entered.set()
        self.release.wait(1.0)

    def destroy(self):
        self.destroyed = True


def test_stop_mic_waits_for_a_live_tick(vr):
    clock = Clock()
    svr = SteamVR(clock=clock, window_mic=True)
    mic = BlockingMic()
    svr.mic = mic
    ticker = threading.Thread(target=svr._tick_mic, args=(0.0,))
    ticker.start()
    assert mic.entered.wait(1.0)
    stopper = threading.Thread(target=svr._stop_mic)
    stopper.start()
    time.sleep(0.1)
    assert not mic.destroyed, "destroy ran while the VR tick was live"
    mic.release.set()
    ticker.join(2.0)
    stopper.join(2.0)
    assert mic.destroyed and svr.mic is None


def test_bar_anchor_is_passed_to_the_controller(vr, monkeypatch):
    svr = SteamVR(clock=Clock(), window_mic=True, window_mic_anchor="bar")
    svr.vr = vr
    import frameassist.window_mic as wm
    monkeypatch.setattr(wm, "unavailable_reason", lambda: None)
    monkeypatch.setattr(wm, "OverlayApi", lambda: object())
    monkeypatch.setattr(wm, "WindowMic", AnchorRecordingMic)
    svr._sync_mic()
    assert svr.mic.anchor == "bar"
    assert svr.mic.hud is None


def test_held_background_relays_mic_clicks_to_the_window():
    """While a window copy is open the background is held; its mic plate must
    stay clickable by relaying to the window's engine."""
    svr = SteamVR(clock=Clock(), window_mic=True, window_mic_anchor="bar")
    svr.engine = object()
    forwarded = []
    svr.forward_command = forwarded.append
    svr.hold = True
    assert svr._mic_can_act() is True
    svr.send_command("talk_start")
    assert forwarded == ["talk_start"]

    # Held with nothing to relay to (e.g. still loading): clicks stay off.
    svr.forward_command = None
    assert svr._mic_can_act() is False

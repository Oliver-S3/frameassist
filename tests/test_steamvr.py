"""Tests that drive the real SteamVR wrapper in front of the fake openvr,
and the Engine's gesture handling with a stubbed recorder / typist."""

import pytest

from frameassist import engine as eng_mod
from frameassist.engine import Engine
from frameassist.gestures import GestureEngine
from frameassist.steamvr import BUTTONS, SteamVR

from .fake_openvr import BUTTON_STATES, make_openvr


class Commands:
    def __init__(self):
        self.commands = []

    def __call__(self, cmd):
        self.commands.append(cmd)


@pytest.fixture
def vr(tmp_path):
    import pathlib

    import frameassist.steamvr as svr_mod
    # Never touch the real ~/.local on any machine: redirect the small files
    # SteamVR writes (vrmanifest, diagnostic json) into the tmp dir.
    svr_mod.DATA_DIR = pathlib.Path(tmp_path / "data")
    svr_mod.DIAG_PATH = pathlib.Path(tmp_path / "state" / "steamvr.json")
    svr_mod.DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)

    from .fake_openvr import make_openvr
    BUTTON_STATES.clear()
    return make_openvr()


class Clock:
    """A controllable time.monotonic for driving hold/tap timings."""

    def __init__(self, start=100.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, step=1 / 60):
        self.t += step


def _make_vr(clock):
    svr = SteamVR(clock=clock)
    got = []
    svr.set_gesture_engine(GestureEngine(lambda cmd: got.append(cmd)))
    return svr, got


def _step_button(svr, button, down, now):
    BUTTON_STATES[button] = down
    svr.step()


def test_hold_both_grips_reaches_engine(vr):
    clock = Clock()
    svr, got = _make_vr(clock)
    svr.open(vr)

    # both grips pressed
    for b in ("grip_l", "grip_r"):
        _step_button(svr, b, True, 0.05)
        clock.advance()
    # hold for a while (past talk_hold_min)
    for _ in range(30):
        svr.step()
        clock.advance()
    assert "talk_start" in got
    for b in ("grip_l", "grip_r"):
        _step_button(svr, b, False, 1.0)
        clock.advance()
    assert got[-1] == "talk_stop"


def test_global_priority_asks_near_top_of_overlay_global_band(vr):
    """The Frame's own UI/desktop pre-empts overlay apps that ask at the bottom
    of the overlay-global band (Min+0x100), so grips only worked in games.
    We must ask near the top of the band (still below the reserved range)
    to stay active while just using the Frame."""
    from frameassist.steamvr import SteamVR
    svr = SteamVR(clock=Clock())
    svr.vr = vr
    svr.open(vr)
    band_lo = vr.k_nActionSetOverlayGlobalPriorityMin
    band_hi = vr.k_nActionSetOverlayGlobalPriorityMax
    assert band_lo <= svr.global_priority <= band_hi
    # near the top: within the top 0.1% of the band, not the bottom
    assert (band_hi - svr.global_priority) <= max(1, (band_hi - band_lo) // 1024)


def test_double_tap_chord_toggles_handsfree(vr):
    clock = Clock()
    svr, got = _make_vr(clock)
    svr.open(vr)
    for _ in range(2):
        for b in ("grip_l", "grip_r"):
            _step_button(svr, b, True, 0.01)
            clock.advance()
        for b in ("grip_l", "grip_r"):
            _step_button(svr, b, False, 0.05)
            clock.advance()
        # wait for the double-tap window to settle
        for _ in range(5):
            svr.step()
            clock.advance()
    assert "handsfree_start" in got
    # a single chord tap stops it
    for b in ("grip_l", "grip_r"):
        _step_button(svr, b, True, 0.3)
        clock.advance()
    for b in ("grip_l", "grip_r"):
        _step_button(svr, b, False, 0.35)
        clock.advance()
    for _ in range(5):
        svr.step()
        clock.advance()
    assert "handsfree_stop" in got


def test_stick_flick_sends_enter(vr):
    clock = Clock()
    svr, got = _make_vr(clock)
    svr.open(vr)
    _step_button(svr, "stick_r", True, 0.01)
    clock.advance()
    _step_button(svr, "stick_r", False, 0.06)
    clock.advance()
    assert got == ["enter"]


def test_window_vr_sets_up_bindings(vr):
    from pathlib import Path
    from frameassist.steamvr import DATA_DIR
    clock = Clock()
    svr, _ = _make_vr(clock)
    svr.open(vr)
    assert svr.sets and set(svr.sets) == set(BUTTONS)
    assert svr.actions and set(svr.actions) == set(BUTTONS)
    appman = vr._instances()["VRApplications"].manifest
    assert appman and "frameassist" in str(appman)


def test_vrmanifest_uses_platform_binary_path_key(tmp_path, monkeypatch):
    """Regression: SteamVR on the Frame (Linux ARM64) selects the executable by
    platform key and its own runtime.vrmanifest uses 'binary_path_linux_arm'.
    Missing/mismatched keys make it log 'must specify binary_path for launch_type
    binary. Skipping', skip registration, and drop all overlay-global input."""
    import json
    import platform
    from frameassist import steamvr as svr_mod
    monkeypatch.setattr(svr_mod, "DATA_DIR", tmp_path)
    path = svr_mod.write_vrmanifest()
    doc = json.loads(path.read_text())
    app = doc["applications"][0]
    assert app["app_key"] == svr_mod.APP_KEY
    assert app["launch_type"] == "binary"
    arch = platform.machine().lower()
    if arch in ("aarch64", "arm64", "armv8l"):
        assert "binary_path_linux_arm" in app, "ARM build must use binary_path_linux_arm"
        assert "binary_path_linux" not in app
    else:
        assert "binary_path_linux" in app
        assert "binary_path_linux_arm" not in app
    assert "binary_path_linux_arm" in app or "binary_path_linux" in app
    assert str(app["action_manifest_path"]).endswith("actions.json")


# -- Engine-level --------------------------------------------------------------
class Recorder:
    def __init__(self, out_path):
        self.out_path = out_path
        self.started = 0
        self.stopped = 0
        self.cancelled = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return str(self.out_path)

    def cancel(self):
        self.cancelled += 1


class FakeTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, path, words=""):
        self.calls.append((path, words))
        return "hello world"


class Typist:
    def __init__(self):
        self.typed = []
        self.shortcuts = []

    def type_text(self, text):
        self.typed.append(text)

    def shortcut(self, name):
        self.shortcuts.append(name)

    def close(self):
        pass


def _wait_until(predicate, timeout=3.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for engine")


def test_engine_talk_flow(tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    rec = Recorder(wav)
    typ = Typist()
    tr = FakeTranscriber()
    clipboard = []

    cfg = {"model": "base.en", "add_space": True, "press_enter": False,
           "after_talking": "type", "ai_polish": False, "ai_key": ""}
    states = []
    texts = []
    e = Engine(cfg, on_state=lambda s, m: states.append((s, m)),
               on_transcript=lambda t: texts.append(t),
               transcriber=tr, typist=typ, recorder=rec,
               copy_clipboard=lambda t: clipboard.append(t))
    e.load()

    e.hotkey({"cmd": "talk_start"})
    assert rec.started == 1
    assert states[-1][0] == eng_mod.LISTENING

    e.hotkey({"cmd": "talk_stop"})
    _wait_until(lambda: bool(texts))
    assert texts == ["hello world"]
    assert typ.typed == ["hello world "]
    assert clipboard == ["hello world"]   # clipboard holds the clean text
    assert states[-1][0] == eng_mod.READY


def test_engine_paste_mode_copies_and_ctrl_v(tmp_path):
    """'after_talking: paste' copies the text then sends Ctrl+V — the path
    that rides Steam Remote Play's clipboard + hotkey forwarding into a
    streamed remote app (per-character uinput typing only reaches the Frame's
    own focused window)."""
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    rec = Recorder(wav)
    typ = Typist()
    tr = FakeTranscriber()
    clipboard = []

    cfg = {"model": "base.en", "add_space": True, "after_talking": "paste",
           "ai_polish": False, "ai_key": ""}
    e = Engine(cfg, transcriber=tr, typist=typ, recorder=rec,
               copy_clipboard=lambda t: clipboard.append(t))
    e.load()
    e.hotkey({"cmd": "talk_start"})
    e.hotkey({"cmd": "talk_stop"})
    _wait_until(lambda: bool(typ.shortcuts))
    assert typ.typed == []                      # no per-character typing
    assert typ.shortcuts == ["paste"]           # Ctrl+V sent
    assert clipboard == ["hello world"]         # clipboard set for the sync
    assert typ.close is not None


def test_engine_copy_pastes(monkeypatch):
    typ = Typist()
    cfg = {"after_talking": "type", "ai_polish": False, "ai_key": ""}
    e = Engine(cfg, transcriber=FakeTranscriber(), typist=typ,
               copy_clipboard=lambda t: None)
    e.last_text = "saved text"
    e.hotkey({"cmd": "copy"})
    assert typ.shortcuts == []
    e.hotkey({"cmd": "paste"})
    assert typ.shortcuts == ["paste"]
    e._typist = typ
    e.hotkey({"cmd": "enter"})
    assert typ.shortcuts == ["paste", "enter"]


def test_frame_ui_vrlink_is_home_not_game():
    """The Steam Frame's own UI (vrlink.client) shows as the scene app while the
    user is just using the Frame. Treating it as a game paused all button
    reading, so grips never fired outside a real game with the Steam menu open.
    It must be classified as home so shortcut reading stays active."""
    from frameassist.steamvr import is_home
    assert is_home("vrlink.client")
    assert is_home("vrlink.bigscreen")
    assert is_home("steam.app.250820")       # SteamVR Home
    assert is_home("system.generated.gamescope")
    # Real steam games stay games.
    assert not is_home("steam.app.1245620")

"""Engine wiring for calibration: while a session is active, talk drives the
chart and flicks navigate instead of typing text."""

from frameassist import calibrate as cal
from frameassist.engine import Engine


class FakeTranscriber:
    def transcribe(self, path, words=""):
        return "save as with spacers"


class Recorder:
    def __init__(self, out):
        self.out = out
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return str(self.out)

    def cancel(self):
        pass


class Typist:
    def __init__(self):
        self.typed = []
        self.shortcuts = []

    def type_text(self, t):
        self.typed.append(t)

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


def test_dictation_routes_to_calibrate_while_active(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    typ = Typist()
    session = cal.CalibrationSession()
    saved = {}
    monkeypatch.setattr(cal, "save_profile",
                        lambda name, test=None, fineness=None, **k: saved.update(
                            {"name": name, "test": test, "fineness": fineness}))
    states = []
    engine = Engine(cfg, transcriber=FakeTranscriber(), typist=typ,
                    recorder=Recorder(wav), copy_clipboard=lambda t: None,
                    on_state=lambda s, m: states.append((s, m)))
    engine.calibrate = session
    session.start()

    engine.hotkey({"cmd": "talk_start"})
    engine.hotkey({"cmd": "talk_stop"})
    _wait_until(lambda: engine.state != "working")
    # the dictation was interpreted as "save as with spacers" -> profile saved
    assert typ.typed == []
    assert saved["name"] == "with spacers"
    assert saved["test"] == session.test


def test_flicks_navigate_calibrate_not_type():
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    typ = Typist()
    session = cal.CalibrationSession()
    engine = Engine(cfg, typist=typ)
    engine.calibrate = session
    session.start()
    start_test = session.test

    engine.hotkey({"cmd": "enter"})
    assert session.test == cal.TESTS[(cal.TESTS.index(start_test) + 1) % len(cal.TESTS)]
    assert typ.shortcuts == []  # didn't press Enter for real

    engine.hotkey({"cmd": "escape"})
    assert session.test == start_test
    assert typ.shortcuts == []


def test_flicks_type_when_not_calibrating():
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    typ = Typist()
    engine = Engine(cfg, typist=typ)
    engine.hotkey({"cmd": "enter"})
    engine.hotkey({"cmd": "escape"})
    assert typ.shortcuts == ["enter", "escape"]

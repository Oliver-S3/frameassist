"""Engine routing for the "Type into a streamed PC" delivery mode.

Follows the fake-typist pattern in tests/test_steamvr.py: a stub recorder, a
stub transcriber, and a fake that stands in for the LAN HostTypist, so the
whole delivery decision is exercised headless with no real host.
"""

import time

from frameassist import engine as eng_mod
from frameassist.engine import Engine
from frameassist.hostlink import HostUnavailable

READY = eng_mod.READY
ERROR = eng_mod.ERROR


class Recorder:
    def __init__(self, out_path):
        self.out_path = out_path
        self.started = 0

    def start(self):
        self.started += 1

    def stop(self):
        return str(self.out_path)

    def cancel(self):
        pass


class FakeTranscriber:
    def transcribe(self, path, words=""):
        return "hello world"


class LocalTypist:
    """The local uinput Typist stand-in: if anything lands here while
    delivery='host', the routing is wrong."""

    def __init__(self):
        self.typed = []
        self.shortcuts = []

    def type_text(self, text):
        self.typed.append(text)

    def shortcut(self, name):
        self.shortcuts.append(name)

    def close(self):
        pass


class FakeHost:
    """Stands in for hostlink.HostTypist. Records the ops the engine sends and
    can be told to fail like an unreachable helper."""

    def __init__(self, fail=False):
        self.ops = []
        self.fail = fail
        self.closed = False

    def _check(self):
        if self.fail:
            raise HostUnavailable("can't reach the PC helper")

    def type_text(self, text):
        self._check()
        self.ops.append(("type", text))

    def shortcut(self, name):
        self._check()
        self.ops.append(("shortcut", name))

    def copy_text(self, text):
        self._check()
        self.ops.append(("copy", text))

    def ping(self):
        return not self.fail

    def close(self):
        self.closed = True


def _host_cfg(**over):
    cfg = {"model": "base.en", "add_space": True, "press_enter": False,
           "after_talking": "type", "ai_polish": False, "ai_key": "",
           "delivery": "host", "host_addr": "192.168.1.50",
           "host_port": 8763, "host_secret": "ABCD-1234"}
    cfg.update(over)
    return cfg


def _engine(cfg, tmp_path, host, local, clipboard, monkeypatch=None):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    if monkeypatch is not None:
        monkeypatch.setattr(eng_mod, "HostTypist", lambda *a, **k: host)
    states = []
    texts = []
    e = Engine(cfg, on_state=lambda s, m: states.append((s, m)),
               on_transcript=lambda t: texts.append(t),
               transcriber=FakeTranscriber(), typist=local,
               recorder=Recorder(wav), copy_clipboard=lambda t: clipboard.append(t))
    e.load()
    e._states = states
    e._texts = texts
    return e


def _wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for engine")


def _wait_done(e):
    # Wait for the terminal state, which _finish_text sets *after* its last
    # log call, so the transcribe daemon thread has finished logging before the
    # test returns (no logging-at-shutdown race with pytest's capture).
    _wait_until(lambda: e.state in (READY, ERROR))


def _dictate(e):
    e.hotkey({"cmd": "talk_start"})
    e.hotkey({"cmd": "talk_stop"})


# -- dictation routing -------------------------------------------------------

def test_dictation_goes_to_host_not_local(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    _dictate(e)
    _wait_done(e)
    assert host.ops == [("type", "hello world ")]   # verbatim text to the host
    assert local.typed == []                        # NOT typed on the Frame
    assert clip == ["hello world"]                  # Frame clipboard still set
    assert e._states[-1][0] == READY


def test_press_enter_adds_enter_op(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    e = _engine(_host_cfg(press_enter=True), tmp_path, host, local, clip, monkeypatch)
    _dictate(e)
    _wait_done(e)
    assert host.ops == [("type", "hello world "), ("shortcut", "enter")]
    assert local.shortcuts == []


# -- MUST-FIX: paste on the host sets the HOST clipboard first ----------------

def test_paste_mode_sends_copy_then_paste(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    e = _engine(_host_cfg(after_talking="paste"), tmp_path, host, local, clip, monkeypatch)
    _dictate(e)
    _wait_done(e)
    # copy FIRST (sets the host clipboard), THEN paste — never paste stale data.
    assert host.ops == [("copy", "hello world"), ("shortcut", "paste")]
    assert local.shortcuts == []                    # no local Ctrl+V
    assert clip == ["hello world"]                  # Frame clipboard still set


def test_paste_mode_skips_local_settle_sleep(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    e = _engine(_host_cfg(after_talking="paste"), tmp_path, host, local, clip, monkeypatch)
    _dictate(e)
    _wait_done(e)
    assert 0.35 not in slept                        # the clipboard-settle hack is skipped
    assert host.ops == [("copy", "hello world"), ("shortcut", "paste")]


# -- hotkey routing ------------------------------------------------------------

def test_hotkey_enter_escape_paste_route_to_host(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    e.hotkey({"cmd": "enter"})
    e.hotkey({"cmd": "escape"})
    e.hotkey({"cmd": "paste"})
    assert host.ops == [("shortcut", "enter"), ("shortcut", "escape"), ("shortcut", "paste")]
    assert local.shortcuts == []


def test_hotkey_copy_sets_host_and_keeps_frame_clipboard(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    e.last_text = "saved text"
    e.hotkey({"cmd": "copy"})
    assert host.ops == [("copy", "saved text")]     # sets the HOST clipboard
    assert clip == ["saved text"]                   # Frame clipboard kept as fallback
    assert e._states[-1][0] == READY


# -- HostUnavailable -> ERROR, clipboard kept, no local typing -----------------

def test_dictation_host_unavailable(tmp_path, monkeypatch):
    host, local, clip = FakeHost(fail=True), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    _dictate(e)
    _wait_done(e)
    assert e._states[-1][0] == ERROR
    assert clip == ["hello world"]                  # clipboard still populated
    assert local.typed == []                        # NO local-typing fallback
    msg = e._states[-1][1]
    assert "192.168.1.50" in msg and "FrameAssistHost" in msg


def test_hotkey_host_unavailable_badges(tmp_path, monkeypatch):
    host, local, clip = FakeHost(fail=True), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    e.hotkey({"cmd": "enter"})
    assert e.state == ERROR
    assert "FrameAssistHost" in e.message
    assert local.shortcuts == []                    # no local fallback


def test_hotkey_copy_host_unavailable_keeps_frame_clipboard(tmp_path, monkeypatch):
    host, local, clip = FakeHost(fail=True), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    e.last_text = "saved text"
    e.hotkey({"cmd": "copy"})
    assert e.state == ERROR
    assert clip == ["saved text"]                   # Frame clipboard still set


# -- the seam itself + caching / rebuild --------------------------------------

def test_need_typist_returns_host_in_host_mode(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    e = _engine(_host_cfg(), tmp_path, host, local, clip, monkeypatch)
    assert e._need_typist() is host                 # the seam routes to the host


def test_need_host_cached_and_rebuilt_on_key_change(tmp_path, monkeypatch):
    built = []

    def factory(*a, **k):
        h = FakeHost()
        built.append((a, h))
        return h

    monkeypatch.setattr(eng_mod, "HostTypist", factory)
    cfg = _host_cfg()
    e = Engine(cfg, transcriber=FakeTranscriber(), typist=LocalTypist(),
               recorder=Recorder(tmp_path / "x.wav"), copy_clipboard=lambda t: None)
    first = e._need_host()
    assert e._need_host() is first                  # cached while key is stable
    assert len(built) == 1
    assert built[0][0] == ("192.168.1.50", 8763, "ABCD-1234")
    # a Settings change to any host_* value rebuilds it
    cfg["host_addr"] = "10.0.0.9"
    second = e._need_host()
    assert second is not first
    assert len(built) == 2
    assert built[1][0] == ("10.0.0.9", 8763, "ABCD-1234")


# -- the default (frame) path is untouched ------------------------------------

def test_frame_delivery_uses_local_typist(tmp_path, monkeypatch):
    host, local, clip = FakeHost(), LocalTypist(), []
    calls = []
    monkeypatch.setattr(eng_mod, "HostTypist", lambda *a, **k: calls.append(1) or host)
    cfg = _host_cfg(delivery="frame")
    e = _engine(cfg, tmp_path, host, local, clip, monkeypatch=None)
    _dictate(e)
    _wait_done(e)
    assert local.typed == ["hello world "]          # local uinput path unchanged
    assert host.ops == []                           # host never touched
    assert calls == []                              # HostTypist never built


def test_frame_paste_mode_still_settles(tmp_path):
    """The local paste path keeps its 0.35 s clipboard-settle (regression)."""
    local, clip = LocalTypist(), []
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    slept = []
    real_sleep = time.sleep

    def spy(s):
        slept.append(s)
        real_sleep(0)  # don't actually stall the test

    import frameassist.engine as em
    # the sleep is imported inside the function as `import time as _t`, so
    # patch the module attribute the call resolves through.
    import time as time_mod
    orig = time_mod.sleep
    time_mod.sleep = spy
    try:
        e = Engine({"model": "base.en", "add_space": True, "after_talking": "paste",
                    "ai_polish": False, "ai_key": "", "delivery": "frame"},
                   transcriber=FakeTranscriber(), typist=local,
                   recorder=Recorder(wav), copy_clipboard=lambda t: clip.append(t))
        e.load()
        e.hotkey({"cmd": "talk_start"})
        e.hotkey({"cmd": "talk_stop"})
        _wait_done(e)
    finally:
        time_mod.sleep = orig
    assert local.shortcuts == ["paste"]
    assert 0.35 in slept                            # local path still settles

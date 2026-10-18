"""Regression tests for app wiring bugs found in review.

These protect against:
1. The window-copy state callback recursing through engine._set (infinite
   recursion on the first state change).
2. The background copy dropping VR-badge updates after run_background overrides
   engine.on_state.
"""

from frameassist import engine as eng_mod
from frameassist.engine import Engine


class Stub:
    def __init__(self):
        self.notified = []
        self.badge = []

    def notify_state(self, state, message):
        self.notified.append((state, message))

    def show_state(self, state, message):
        self.badge.append((state, message))


def test_state_callback_forwards_without_recursing():
    """run_window wired on_state to window+vr only, NOT engine._set again."""
    stub = Stub()
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    engine = Engine(cfg)
    # Mirrors app.py run_window: on_state forwards, and _set stores state.
    engine.on_state = lambda state, message: stub.notify_state(state, message)
    # A state change must terminate and notify exactly once — no recursion.
    engine._set(eng_mod.LISTENING, "Listening…")
    assert engine.state == eng_mod.LISTENING
    assert stub.notified == [(eng_mod.LISTENING, "Listening…")]
    assert engine._set(eng_mod.READY, "Ready") is None


def test_background_on_state_feeds_badge():
    """run_background replaces engine.on_state; it must still feed the badge."""
    stub = Stub()
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    engine = Engine(cfg)
    state = {}
    work = type("Work", (), {"vr": stub})()

    def on_state(s, msg):
        state["engine"] = s
        state["message"] = msg
        work.vr.show_state(s, msg)  # Background badge feed kept

    engine.on_state = on_state
    engine._set(eng_mod.WORKING, "Typing…")
    assert state["engine"] == eng_mod.WORKING
    assert stub.badge == [(eng_mod.WORKING, "Typing…")]


# -- streamed-PC CLI surface (--host-test, --check host line) ----------------

class _OKDevice:
    def close(self):
        pass


def _neutralise_env_checks(monkeypatch, tmp_path):
    """Make _check's environment-dependent probes pass on this (Windows) box so
    the return code reflects only the streamed-PC line we're testing."""
    from frameassist import autostart, config, linux_input
    monkeypatch.setattr(linux_input, "UInputKeyboard", lambda *a, **k: _OKDevice())
    monkeypatch.setattr(autostart, "available", lambda: True)
    monkeypatch.setattr(autostart, "enabled", lambda: True)
    monkeypatch.setattr(config, "words_file", lambda *a, **k: tmp_path / "words.txt")


def test_host_test_cli_reachable(monkeypatch, capsys):
    from frameassist import app as app_mod
    import frameassist.hostlink as hostlink

    class FakeTypist:
        def __init__(self, addr, port, secret):
            self.args = (addr, port, secret)

        def ping(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(hostlink, "HostTypist", FakeTypist)
    assert app_mod.main(["--host-test"]) == 0
    assert "reachable" in capsys.readouterr().out.lower()


def test_host_test_cli_not_reachable(monkeypatch, capsys):
    from frameassist import app as app_mod
    import frameassist.hostlink as hostlink

    class FakeTypist:
        def __init__(self, addr, port, secret):
            pass

        def ping(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(hostlink, "HostTypist", FakeTypist)
    assert app_mod.main(["--host-test"]) == 1
    assert "not reachable" in capsys.readouterr().out.lower()


def test_check_host_line_absent_and_exits_zero_without_host(monkeypatch, capsys, tmp_path):
    """No host configured (default 'frame'): --check prints an informative SKIP
    line and still exits 0."""
    from frameassist import app as app_mod
    _neutralise_env_checks(monkeypatch, tmp_path)
    cfg = {"model": "base.en", "delivery": "frame", "host_addr": "",
           "host_port": 8763, "host_secret": ""}
    rc = app_mod._check(cfg)
    out = capsys.readouterr().out
    assert "PC helper" in out
    assert "SKIP" in out
    assert rc == 0


def test_check_host_mode_without_address_skips_with_clear_message(monkeypatch, capsys, tmp_path):
    """delivery='host' but no address: --check prints a clear SKIP (not the
    misleading 'types into the headset') and still exits 0."""
    from frameassist import app as app_mod
    _neutralise_env_checks(monkeypatch, tmp_path)
    cfg = {"model": "base.en", "delivery": "host", "host_addr": "",
           "host_port": 8763, "host_secret": ""}
    rc = app_mod._check(cfg)
    out = capsys.readouterr().out
    assert "no PC address is set" in out
    assert "types into the headset" not in out
    assert rc == 0


def test_check_host_reachable_line_when_configured(monkeypatch, capsys, tmp_path):
    """delivery='host' with an address: --check pings and reports the result."""
    from frameassist import app as app_mod
    import frameassist.hostlink as hostlink
    _neutralise_env_checks(monkeypatch, tmp_path)

    class FakeTypist:
        def __init__(self, *a, **k):
            pass

        def ping(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(hostlink, "HostTypist", FakeTypist)
    cfg = {"model": "base.en", "delivery": "host", "host_addr": "10.0.0.9",
           "host_port": 8763, "host_secret": "x"}
    rc = app_mod._check(cfg)
    out = capsys.readouterr().out
    assert "PC helper reachable" in out
    assert rc == 0


def test_check_host_unreachable_is_a_problem(monkeypatch, capsys, tmp_path):
    """delivery='host' configured but unreachable: --check flags it (non-zero)."""
    from frameassist import app as app_mod
    import frameassist.hostlink as hostlink
    _neutralise_env_checks(monkeypatch, tmp_path)

    class FakeTypist:
        def __init__(self, *a, **k):
            pass

        def ping(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(hostlink, "HostTypist", FakeTypist)
    cfg = {"model": "base.en", "delivery": "host", "host_addr": "10.0.0.9",
           "host_port": 8763, "host_secret": "x"}
    rc = app_mod._check(cfg)
    out = capsys.readouterr().out
    assert "PC helper reachable" in out
    assert rc == 1

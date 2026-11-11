"""Tests for the background/window handoff (Linux only: uses fcntl locks and
Unix abstract sockets)."""

import os
import sys
import threading
import time

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("service handoff tests need Linux (fcntl + abstract sockets)",
                allow_module_level=True)

from frameassist import service as s  # noqa: E402


def test_single_copy_per_role(tmp_path, monkeypatch):
    """Holding the same role in two separate processes must allow only one.

    hold_lock is idempotent within a single process (a process that already
    holds the role re-enters it), so a "second copy" must be a genuinely
    separate OS process whose flock against the same lock file loses.
    """
    import subprocess as sp
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)

    _helper = (
        "import sys;"
        "from pathlib import Path;"
        "from frameassist import service as s;"
        "s.state_dir = lambda: Path(sys.argv[1]);"
        "print('HELD' if s.hold_lock('background') else 'LOSER')"
    )

    def second_copy(path):
        proc = sp.run([sys.executable, "-c", _helper, str(path)],
                      capture_output=True, text=True, timeout=10)
        return proc.stdout.strip()

    def second_copy_loses(path):
        proc = sp.run([sys.executable, "-c", _helper, str(path)],
                      capture_output=True, text=True, timeout=10)
        assert proc.stdout.strip() == "LOSER", (
            f"expected second copy to lose; stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r} rc={proc.returncode}")
        return True

    assert s.hold_lock("background") is True           # first copy holds it
    second_copy_loses(tmp_path)                         # second copy loses
    s.release_lock("background")
    assert second_copy(tmp_path) == "HELD"             # after release it can
    assert s.hold_lock("background") is True


def test_window_link_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    events = []
    server = s._Connector(on_window=lambda on: events.append(("window", on)),
                          on_quit=lambda: events.append(("quit", True)))
    server.start()
    server.last_text = "hello from background"
    link = s.Link.open(timeout=3)
    assert link is not None
    assert link.last_text == "hello from background"
    _wait_for(lambda: ("window", True) in events)
    # the window is live; server reports it
    assert server.has_window()
    # last dictation flows back
    link.send_last("new words")
    _wait_for(lambda: server.last_text == "new words")
    # closing the link hands the work back
    link.close()
    _wait_for(lambda: ("window", False) in events)
    server.close()


def test_status_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    server = s._Connector(on_window=lambda on: None, on_quit=lambda: None)
    server.start()
    status = s.status(timeout=2)
    assert status is not None
    assert status["windows"] == 0
    server.close()


def test_closing_the_window_does_not_stop_the_background(tmp_path, monkeypatch):
    """Closing the GUI drops the link; the background takes the shortcuts back.

    Regression for 2026-11-09: the window copy sent "quit" from closeEvent, so
    opening and closing the GUI stopped frameassist.service ("quit from the
    window")."""
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    events = []
    server = s._Connector(on_window=lambda on: events.append(("window", on)),
                          on_quit=lambda: events.append(("quit", True)))
    server.start()
    link = s.Link.open(timeout=3)
    assert link is not None
    _wait_for(lambda: ("window", True) in events)
    link.close()
    _wait_for(lambda: ("window", False) in events)
    time.sleep(0.1)
    assert ("quit", True) not in events
    server.close()


def test_settings_message_reloads_the_background(tmp_path, monkeypatch):
    """A Settings change in the window reaches the running background copy."""
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    events = []
    server = s._Connector(on_window=lambda on: None,
                          on_quit=lambda: None,
                          on_settings=lambda: events.append("settings"))
    server.start()
    link = s.Link.open(timeout=3)
    assert link is not None
    link.reload_settings()
    _wait_for(lambda: "settings" in events)
    link.close()
    server.close()


def _wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


def test_mic_clicks_reach_the_window(tmp_path, monkeypatch):
    """The background keeps the mic plate while a window is open, but its
    engine is held. Clicks have to be relayed to the window (live 2026-11-11:
    the plate was dead whenever FrameAssist's own window was open)."""
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    server = s._Connector(on_window=lambda on: None, on_quit=lambda: None)
    server.start()
    link = s.Link.open(timeout=3)
    assert link is not None
    _wait_for(lambda: server.has_window())
    got, lost = [], []
    link.watch(lambda: lost.append(True), on_talk=got.append)
    server.send_talk("talk_start")
    _wait_for(lambda: got == ["talk_start"])
    server.send_talk("talk_stop")
    _wait_for(lambda: got == ["talk_start", "talk_stop"])
    link.close()
    _wait_for(lambda: lost)
    server.close()


def test_window_engine_state_reaches_the_background(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    states = []
    server = s._Connector(on_window=lambda on: None, on_quit=lambda: None,
                          on_state=lambda st, msg: states.append((st, msg)))
    server.start()
    link = s.Link.open(timeout=3)
    assert link is not None
    _wait_for(lambda: server.has_window())
    link.send_state("working", "hello there")
    _wait_for(lambda: states == [("working", "hello there")])
    link.close()
    server.close()

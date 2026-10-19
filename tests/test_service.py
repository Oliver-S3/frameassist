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
    monkeypatch.setattr(s, "state_dir", lambda: tmp_path)
    assert s.hold_lock("background") is True
    assert s.hold_lock("background") is False  # second copy loses
    s.release_lock("background")
    assert s.hold_lock("background") is True
    s.release_lock("background")


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


def _wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for condition")

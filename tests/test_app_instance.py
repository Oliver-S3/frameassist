"""Tests for the one-window-copy lock."""

import os

from frameassist import app as app_mod


def test_claim_creates_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "_pid_alive", lambda pid: False)
    path = app_mod.claim_window_instance(tmp_path)
    assert path == tmp_path / "window.pid"
    assert path.read_text().strip() == str(os.getpid())


def test_claim_refuses_when_another_copy_is_alive(tmp_path, monkeypatch):
    (tmp_path / "window.pid").write_text("4242")
    monkeypatch.setattr(app_mod, "_pid_alive", lambda pid: pid == 4242)
    assert app_mod.claim_window_instance(tmp_path) is None


def test_claim_takes_over_a_stale_pid(tmp_path, monkeypatch):
    path = tmp_path / "window.pid"
    path.write_text("4242")
    monkeypatch.setattr(app_mod, "_pid_alive", lambda pid: False)
    assert app_mod.claim_window_instance(tmp_path) == path
    assert path.read_text().strip() == str(os.getpid())


def test_release_removes_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "_pid_alive", lambda pid: False)
    path = app_mod.claim_window_instance(tmp_path)
    app_mod.release_window_instance(path)
    assert not path.exists()


def test_pid_alive_is_posix_only(monkeypatch):
    monkeypatch.setattr(app_mod.os, "name", "nt")
    assert app_mod._pid_alive(1) is False

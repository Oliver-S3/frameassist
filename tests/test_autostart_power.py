"""Tests for autostart.power (turn the app fully off/on from the menu)."""

import pytest

from frameassist import autostart as A


class FakeCtl:
    """Replaces systemctl/systemd interactions in autostart with a record."""

    def __init__(self):
        self.calls = []
        self._enabled = True
        self._active = True
        self._available = True

    def systemctl(self, *args):
        self.calls.append(args)
        if args[0] == "is-enabled":
            return type("P", (), {"returncode": 0,
                                  "stdout": "enabled\n" if self._enabled else "disabled\n",
                                  "stderr": ""})()
        if args[0] == "is-active":
            return type("P", (), {"returncode": 0,
                                  "stdout": "active\n" if self._active else "inactive\n",
                                  "stderr": ""})()
        if args[0] == "disable":
            self._enabled = False
        if args[0] == "enable":
            self._enabled = True
            self._active = True
        if args[0] == "stop":
            self._active = False
        if args[0] == "start":
            self._active = True
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def available(self):
        return self._available


@pytest.fixture
def fake(monkeypatch):
    f = FakeCtl()
    monkeypatch.setattr(A, "systemctl", f.systemctl)
    monkeypatch.setattr(A, "available", f.available)
    return f


def test_power_off_stops_and_disables(fake):
    ok, summary = A.power(False)
    assert ok
    assert "Off" in summary
    assert not A.enabled()
    assert not A.is_active()
    assert ("stop", A.UNIT) in fake.calls
    assert ("disable", A.UNIT) in fake.calls


def test_power_on_enables_and_starts(fake, tmp_path, monkeypatch):
    # Redirect the systemd unit dir so enabling doesn't touch the real home.
    monkeypatch.setattr(A, "UNIT_DIR", tmp_path)
    fake._enabled = False
    fake._active = False
    ok, summary = A.power(True)
    assert ok
    assert A.enabled()
    assert A.is_active()
    assert ("enable", A.UNIT) in fake.calls
    assert ("start", A.UNIT) in fake.calls


def test_power_off_when_already_off_is_ok(fake):
    A.power(False)
    fake.calls.clear()
    ok, summary = A.power(False)
    assert ok
    assert "Off" in summary


def test_power_on_enables_and_starts(fake, tmp_path, monkeypatch):
    # Route the unit-file write into the workspace-rooted writable tmp dir.
    monkeypatch.setattr(A, "UNIT_DIR", tmp_path)
    fake._enabled = False
    fake._active = False
    ok, summary = A.power(True)
    assert ok
    assert A.enabled()
    assert A.is_active()
    assert ("enable", A.UNIT) in fake.calls
    assert ("start", A.UNIT) in fake.calls


def test_power_on_reports_setup_failure_when_unavailable(fake):
    fake._available = False
    ok, summary = A.power(True)
    assert not ok
    assert "Couldn't" in summary


def test_menu_wiring_constructs():
    """App menu has Turn off / Turn on actions wired through MainWindow."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from frameassist.engine import Engine
    from frameassist.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    cfg = {"add_space": True, "model": "base.en", "autostart": True}
    engine = Engine(cfg)
    calls = []
    window = MainWindow(cfg, engine, on_power=lambda on: (True, "off" if not on else "on"))
    # the App menu exists with both actions
    menu = window.menuBar()
    assert menu is not None
    assert window.power_off_action is not None
    assert window.power_on_action is not None
    window.close()

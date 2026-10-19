"""Light UI tests. Skip when PySide6 isn't installed (the pure-logic tests
never need Qt; these verify the window can be constructed offscreen)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from frameassist.ui import MainWindow, SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_window_constructs(app):
    from frameassist.engine import Engine
    cfg = {"add_space": True, "model": "base.en"}
    engine = Engine(cfg)
    window = MainWindow(cfg, engine, on_settings=lambda cfg: None)
    assert window.windowTitle() == "FrameAssist"
    window.close()


def test_settings_dialog_values(app):
    cfg = {"model": "base.en", "add_space": True, "press_enter": False,
           "after_talking": "type", "in_games": False, "autostart": True,
           "ai_polish": True, "ai_key": "sk-test",
           "ai_base_url": "http://localhost:11434/v1", "ai_model": "qwen2.5:7b"}
    dialog = SettingsDialog(cfg)
    values = dialog.values()
    assert values["model"] == "base.en"
    assert values["add_space"] is True
    assert values["ai_polish"] is True
    assert values["ai_key"] == "sk-test"
    assert values["ai_base_url"] == "http://localhost:11434/v1"
    assert values["ai_model"] == "qwen2.5:7b"
    dialog.close()


def test_settings_dialog_host_fields_toggle(app):
    """Host fields appear only in 'host' delivery mode; delivery is restored
    from cfg; the secret is masked; values() carries the four new keys."""
    from PySide6.QtWidgets import QLineEdit
    cfg = {"model": "base.en", "add_space": True, "press_enter": False,
           "after_talking": "type", "in_games": False, "autostart": True,
           "ai_polish": False, "ai_key": "",
           "delivery": "frame", "host_addr": "10.0.0.5", "host_port": 9000,
           "host_secret": "s3cr3t"}
    dialog = SettingsDialog(cfg)

    # frame mode (restored from cfg): host fields hidden.
    assert dialog.delivery.currentData() == "frame"
    assert dialog.host_addr.isHidden()
    assert dialog.host_port.isHidden()
    assert dialog.host_secret.isHidden()

    # switch to host mode: fields appear.
    dialog.delivery.setCurrentIndex(1)
    assert dialog.delivery.currentData() == "host"
    assert not dialog.host_addr.isHidden()
    assert not dialog.host_port.isHidden()
    assert not dialog.host_secret.isHidden()

    # the pairing code is masked.
    assert dialog.host_secret.echoMode() == QLineEdit.Password

    # values() carries the four new keys, port as an int.
    values = dialog.values()
    assert values["delivery"] == "host"
    assert values["host_addr"] == "10.0.0.5"
    assert values["host_port"] == 9000
    assert values["host_secret"] == "s3cr3t"

    # back to frame mode: hidden again.
    dialog.delivery.setCurrentIndex(0)
    assert dialog.host_addr.isHidden()

    # a non-numeric port falls back to the default int, never a crash.
    dialog.delivery.setCurrentIndex(1)
    dialog.host_port.setText("not-a-number")
    assert dialog.values()["host_port"] == 8763
    dialog.close()


def test_settings_dialog_delivery_restored_host(app):
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "delivery": "host", "host_addr": "", "host_port": 8763,
           "host_secret": ""}
    dialog = SettingsDialog(cfg)
    assert dialog.delivery.currentData() == "host"
    assert not dialog.host_addr.isHidden()
    dialog.close()


def test_settings_dialog_test_connection(app, monkeypatch):
    """The Test connection button builds a HostTypist from the fields and pings
    it, mirroring the Check-for-updates pattern (button re-enabled, result shown)."""
    import frameassist.hostlink as hostlink
    calls = {}

    class FakeTypist:
        def __init__(self, addr, port, secret):
            calls["args"] = (addr, port, secret)

        def ping(self):
            return True

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(hostlink, "HostTypist", FakeTypist)
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "delivery": "host", "host_addr": "1.2.3.4", "host_port": 8763,
           "host_secret": "abc"}
    dialog = SettingsDialog(cfg)
    dialog._test_connection()
    assert calls["args"] == ("1.2.3.4", 8763, "abc")
    assert calls.get("closed") is True
    assert "reachable" in dialog.test_conn_label.text().lower()
    assert dialog.test_conn_btn.isEnabled()
    dialog.close()


def test_run_window_wiring_smoke(monkeypatch):
    """Run the real window-copy setup (offscreen) to catch wiring bugs: the
    engine state callback must forward without recursing, and the transcript
    hook must be attached after the window exists.

    Linux-only: run_window imports the service handoff module, which uses
    fcntl and Unix sockets unavailable on Windows."""
    import sys
    if not sys.platform.startswith("linux"):
        pytest.skip("run_window imports Linux-only service module")

    from frameassist import service
    from frameassist import app as app_mod

    monkeypatch.setattr(service, "status", lambda *a, **k: None)
    monkeypatch.setattr(service, "Link", type("Link", (), {"open": staticmethod(
        lambda *a, **k: None)}))

    # Don't wait on the real event loop or touch real SteamVR hardware/files.
    monkeypatch.setattr(app_mod.QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(app_mod, "_install_kwin_rule", lambda: None)

    cfg = {"model": "base.en", "add_space": True, "in_games": False,
           "autostart": False, "after_talking": "type", "ai_polish": False,
           "ai_key": "", "no_model": True}

    # run_window builds everything; engine.load is started on a daemon thread,
    # so keep no_model set and let the thread die with the test.
    assert app_mod.run_window(cfg) == 0
    # Reaching here means wiring (window created before hooks attached) worked.

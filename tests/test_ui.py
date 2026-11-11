"""Light UI tests. Skip when PySide6 isn't installed (the pure-logic tests
never need Qt; these verify the window can be constructed offscreen)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QAbstractButton, QApplication, QPushButton  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from frameassist.ui import CalibrateDialog, MainWindow, SettingsDialog  # noqa: E402


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


def test_main_window_never_takes_keyboard_focus(app):
    """A dictation's trailing space used to press the focused mic button and
    start a second, latched take (live 2026-11-10). Every control must be
    click-only and the window must refuse keyboard focus."""
    from frameassist.engine import Engine
    cfg = {"add_space": True, "model": "base.en"}
    engine = Engine(cfg)
    window = MainWindow(cfg, engine, on_settings=lambda cfg: None)
    assert window.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    for btn in window.findChildren(QAbstractButton):
        assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus, btn.text()
    window.close()


def test_open_settings_reloads_the_file_first(app, monkeypatch):
    """Saving a stale dialog used to overwrite a newer Mic position (live
    2026-11-10: window saved over bar after another copy changed it)."""
    import frameassist.ui as ui
    from frameassist.config import DEFAULTS
    from frameassist.engine import Engine
    seen = {}

    class FakeDialog:
        def __init__(self, cfg, parent=None):
            seen["anchor"] = cfg.get("window_mic_anchor")

        def exec(self):
            return 0  # cancel, so nothing is saved

        def values(self):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(ui, "load",
                        lambda: dict(DEFAULTS, window_mic_anchor="bar"))
    monkeypatch.setattr(ui, "SettingsDialog", FakeDialog)
    cfg = dict(DEFAULTS, window_mic_anchor="window")
    window = MainWindow(cfg, Engine(cfg), on_settings=lambda c: None)
    window._open_settings()
    assert seen["anchor"] == "bar"
    # Updated in place: the Engine holds this same dict, so live settings
    # (delivery, host window, ...) still reach it.
    assert window.cfg is cfg
    assert cfg["window_mic_anchor"] == "bar"
    window.close()


def test_open_settings_updates_the_engine_config_in_place(app, monkeypatch):
    """Replacing the dict on reload stranded live settings like delivery (the
    user had to restart the app to switch streamed/local typing)."""
    import frameassist.ui as ui
    from frameassist.config import DEFAULTS
    from frameassist.engine import Engine

    class AcceptingDialog:
        def __init__(self, cfg, parent=None):
            self.cfg = cfg

        def exec(self):
            return 1

        def values(self):
            return {"delivery": "frame", "window_mic_anchor": "hud"}

        def close(self):
            pass

    monkeypatch.setattr(ui, "load", lambda: dict(DEFAULTS, delivery="host"))
    monkeypatch.setattr(ui, "SettingsDialog", AcceptingDialog)
    monkeypatch.setattr(ui, "save", lambda cfg, path=None: None)
    cfg = dict(DEFAULTS, delivery="host", window_mic_anchor="bar")
    engine = Engine(cfg)
    window = MainWindow(cfg, engine, on_settings=lambda c: None)
    window._open_settings()
    assert engine.cfg is cfg
    assert engine.cfg["delivery"] == "frame"
    assert cfg["window_mic_anchor"] == "hud"
    window.close()


def test_open_settings_freezes_the_anchor_around_the_dialog(app, monkeypatch):
    import frameassist.ui as ui
    from frameassist.config import DEFAULTS
    from frameassist.engine import Engine
    calls = []

    class FakeDialog:
        def __init__(self, cfg, parent=None):
            pass

        def exec(self):
            calls.append("exec")
            return 0

        def values(self):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(ui, "load", lambda: dict(DEFAULTS))
    monkeypatch.setattr(ui, "SettingsDialog", FakeDialog)
    cfg = dict(DEFAULTS)
    window = MainWindow(cfg, Engine(cfg), on_settings=lambda c: None,
                        on_settings_open=lambda on: calls.append(on))
    window._open_settings()
    assert calls == [True, "exec", False]
    window.close()


def test_calibrate_dialog_has_fov_button_and_instructions(app):
    import types
    engine = types.SimpleNamespace(calibrate=None)
    dialog = CalibrateDialog(engine, overlay_factory=lambda: None)
    texts = [b.text() for b in dialog.findChildren(QPushButton)]
    assert "FOV / fit" in texts
    assert "Resolution" in texts and "Sweet spot" in texts
    # The FOV quick-jump updates the on-screen instruction.
    dialog.session.show_test("fov")
    assert "strap" in dialog.instructions.text()
    assert dialog.session.test == "fov"
    dialog.close()


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
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(service, "status", lambda *a, **k: None)
    monkeypatch.setattr(service, "Link", type("Link", (), {"open": staticmethod(
        lambda *a, **k: None)}))

    # Don't wait on the real event loop or touch real SteamVR hardware/files.
    # run_window imports QApplication locally, so patch the class method it
    # actually calls rather than an (absent) module attribute.
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(app_mod, "_install_kwin_rule", lambda: None)

    cfg = {"model": "base.en", "add_space": True, "in_games": False,
           "autostart": False, "after_talking": "type", "ai_polish": False,
           "ai_key": "", "no_model": True}

    # run_window builds everything; engine.load is started on a daemon thread,
    # so keep no_model set and let the thread die with the test.
    assert app_mod.run_window(cfg) == 0
    # Reaching here means wiring (window created before hooks attached) worked.

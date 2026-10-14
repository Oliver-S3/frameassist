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
           "ai_polish": False, "ai_key": ""}
    dialog = SettingsDialog(cfg)
    values = dialog.values()
    assert values["model"] == "base.en"
    assert values["add_space"] is True
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

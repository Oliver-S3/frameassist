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

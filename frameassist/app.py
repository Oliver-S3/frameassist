"""FrameAssist entry point.

Runs as a window (``frameassist``), as the background copy SteamVR starts
(``frameassist --background``), or as a diagnostic (``frameassist --check``).
"""

import argparse
import sys
import threading

from .config import load
from .log import log, setup_logging


def _copy_clipboard(text):
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.clipboard().setText(text)
    except Exception:
        pass


def _install_kwin_rule():
    try:
        from .focus import install_kwin_rule
        install_kwin_rule()
    except Exception as err:
        log.info("kwin rule install skipped: %s", err)


def run_window(cfg):
    """The window copy. Installs the KWin rule, starts the speech model load,
    connects its gestures to SteamVR (stepping aside if a background copy has
    the shortcuts), and shows the window."""
    from PySide6.QtWidgets import QApplication
    from .engine import Engine
    from . import service
    from .steamvr import SteamVR
    from .gestures import GestureEngine
    from .ui import MainWindow, _style

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _style(app)
    _install_kwin_rule()

    # The background copy (if any) hands the shortcuts to us while our window
    # is open; it takes them back the moment this window closes.
    background_running = service.status() is not None
    link = service.Link.open() if background_running else None

    engine = Engine(cfg, copy_clipboard=_copy_clipboard)
    vr = SteamVR(in_games=cfg.get("in_games", False),
                 autolaunch=cfg.get("autostart", True))
    vr.set_gesture_engine(GestureEngine(lambda cmd: engine.hotkey({"cmd": cmd})))

    def _on_settings_changed(new_cfg):
        vr.set_options(in_games=new_cfg.get("in_games", False))

    def _on_quit():
        if link:
            link.quit()
            link.close()
        vr.stop()
        engine.close()
        app.quit()

    window = MainWindow(cfg, engine, on_settings=_on_settings_changed,
                        on_quit=_on_quit, link=link,
                        on_turn_on=vr.enable_global_input)

    def _calibrate_overlay_factory():
        # Only build the VR overlay when SteamVR is actually connected.
        if vr.connected:
            from .calibrate import CalibrationOverlay
            return CalibrationOverlay(vr.vr)
        return None

    window.calibrate_overlay = _calibrate_overlay_factory
    # Wire state/transcript to the window AND the VR badge. engine._set already
    # stores (state, message) and calls on_state, so on_state just forwards.
    engine.on_state = lambda state, message: (
        window.notify_state(state, message) or vr.show_state(state, message))
    engine.on_transcript = window.notify_transcript
    window.show()
    vr.start()
    threading.Thread(target=engine.load, daemon=True).start()
    code = app.exec()
    engine.close()
    return code or 0


def _run_background(cfg):
    from .engine import Engine
    from . import service
    return service.run_background(cfg, Engine)


def _check(cfg):
    """Print a diagnostic report. Exits non-zero if a fixable problem is found."""
    from .log import DATA_DIR, STATE_DIR

    lines, problems = [], []

    def report(name, good, detail=""):
        lines.append(f"{'OK ' if good else 'FIX'} {name}{('  — ' + detail) if detail else ''}")
        if not good:
            problems.append(name)

    try:
        from .linux_input import UInputKeyboard
        kb = UInputKeyboard()
        kb.close()
        report("Can type into apps", True)
    except Exception as err:
        report("Can type into apps", False, str(err))

    try:
        import openvr  # noqa: F401
        report("SteamVR library loads", True)
    except Exception as err:
        report("SteamVR library loads", False, str(err))

    try:
        from . import autostart
        started_with = autostart.available() and autostart.enabled()
        report("Starts with SteamVR", bool(started_with))
    except Exception as err:
        report("Starts with SteamVR", False, str(err))

    try:
        from .config import words_file
        report("Settings and words", True, str(words_file()))
    except Exception as err:
        report("Settings and words", False, str(err))

    model = cfg.get("model", "base.en")
    try:
        import faster_whisper  # noqa: F401
        report(f"Speech model ({model}) importable", True)
    except Exception as err:
        report(f"Speech model ({model}) importable", False, str(err))

    lines.append("")
    lines.append(f"State dir: {STATE_DIR}")
    lines.append(f"Data dir:  {DATA_DIR}")
    print("\n".join(lines))
    return 1 if problems else 0


def _register(cfg):
    """Tell SteamVR about the app and set up starting with it. Prints a line
    the installer greps for."""
    from . import autostart
    ok = autostart.set_enabled(True)
    print("starts with SteamVR" if ok else "could not start with SteamVR")
    return 0


def _turn_on(cfg):
    """Flip SteamVR's 'Enable global input from overlays' to on.

    For the headset shell: a CLI way to do what the window's Turn on button
    does, without needing SteamVR connected to a desktop session.
    """
    import openvr
    from .steamvr import GLOBAL_SETTING
    try:
        # Background init doesn't start SteamVR; it just fails if SteamVR
        # isn't running (and it must be, to write its settings).
        openvr.init(openvr.VRApplication_Background)
        try:
            openvr.VRSettings().setBool(*GLOBAL_SETTING, True)
        finally:
            openvr.shutdown()
        print("enabled")
        return 0
    except Exception as err:
        print(f"could not enable: {err}")
        return 1


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="frameassist",
        description="FrameAssist: hold both grips and talk on the Steam Frame.")
    parser.add_argument("--background", action="store_true",
                        help="run the shortcuts without a window (started by SteamVR)")
    parser.add_argument("--check", action="store_true", help="print a diagnostic report")
    parser.add_argument("--register", action="store_true",
                        help="set up starting with SteamVR (used by the installer)")
    parser.add_argument("--turn-on", action="store_true",
                        help="enable SteamVR's 'Enable global input from overlays'")
    parser.add_argument("--version", action="version",
                        version=f"FrameAssist {__import__('frameassist').__version__}")
    args = parser.parse_args(argv)

    setup_logging()
    cfg = load()

    if args.check:
        return _check(cfg)
    if args.register:
        return _register(cfg)
    if args.turn_on:
        return _turn_on(cfg)
    if args.background:
        return _run_background(cfg)
    return run_window(cfg)


if __name__ == "__main__":
    sys.exit(main())

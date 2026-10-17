"""The FrameAssist window (PySide6).

A round mic button, a status line, the last dictation with a Copy button,
Settings (speech model, space/Enter, in-games, auto-start, AI polish), and
"My words". On KDE a window rule keeps our window from taking keyboard focus,
and the window ignores typed keys so it can never be typed into.
"""

import sys

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from .config import MODELS, load, save
from .engine import ERROR, LISTENING, READY, WORKING

DARK_BG = "#0d1016"
CARD = "#161b24"
TEXT = "#eef1f6"
MUTED = "#8b94a7"
ACCENT = "#2ee6b8"
RED = "#ff4d5e"

STATE_COLORS = {
    READY: ACCENT,
    LISTENING: RED,
    WORKING: "#ffb547",
    ERROR: RED,
}
STATE_TEXT = {
    READY: "Ready",
    LISTENING: "Listening…",
    WORKING: "Typing…",
    ERROR: "Error",
}


def _style(app):
    # Lowercase application name = the WM_CLASS KWin rules (and focus.py) key
    # on. Display name stays "FrameAssist"; setApplicationName drives the
    # window class/app-id on X11 and Wayland.
    app.setApplicationName("frameassist")
    app.setApplicationDisplayName("FrameAssist")
    app.setStyleSheet(f"""
        QWidget {{ background: {DARK_BG}; color: {TEXT}; font-family: Inter, "Noto Sans", Cantarell, "DejaVu Sans"; font-size: 15px; }}
        QPushButton {{ background: {CARD}; border: 1px solid #232b38; border-radius: 10px; padding: 9px 16px; }}
        QPushButton:hover {{ border-color: {ACCENT}; }}
        QPushButton:disabled {{ color: {MUTED}; }}
        QLineEdit, QComboBox, QTextEdit {{ background: {CARD}; border: 1px solid #232b38; border-radius: 8px; padding: 7px; }}
        QLabel#title {{ font-size: 22px; font-weight: 700; }}
        QLabel#status {{ color: {MUTED}; }}
        QLabel#last {{ background: {CARD}; border-radius: 10px; padding: 12px; }}
        QLabel#muted {{ color: {MUTED}; font-size: 13px; }}
    """)


class MicButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._listening = False
        self._radius = 54
        self.setFixedSize(132, 132)
        self.setCheckable(False)
        self.setCursor(Qt.PointingHandCursor)
        self.mode = "tap"

    def set_listening(self, on):
        self._listening = on
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(RED if self._listening else ACCENT)
        ring = QColor(color)
        ring.setAlpha(70)
        p.setPen(QPen(ring, 14))
        p.setBrush(QColor(CARD))
        p.drawEllipse(6, 6, self.width() - 12, self.height() - 12)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        if self._listening:
            p.drawRoundedRect(self.width() / 2 - 24, self.height() / 2 - 30, 48, 60, 6, 6)
        else:
            cx, cy = self.width() / 2, self.height() / 2
            p.drawEllipse(cx - 16, cy - 16, 32, 32)
            p.drawRoundedRect(cx - 4, cy - 24, 8, 16, 2, 2)
            p.drawRoundedRect(cx - 4, cy + 8, 8, 16, 2, 2)
        p.end()


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        form = QFormLayout(self)

        self.model = QComboBox()
        for key, label in MODELS.items():
            self.model.addItem(label, key)
        self.model.setCurrentIndex(list(MODELS).index(cfg["model"]) if cfg["model"] in MODELS else 0)
        form.addRow("Speech model", self.model)

        self.add_space = QCheckBox("Add a space after each dictation")
        self.add_space.setChecked(cfg.get("add_space", True))
        form.addRow("Text", self.add_space)

        self.press_enter = QCheckBox("Press Enter after each dictation")
        self.press_enter.setChecked(cfg.get("press_enter", False))
        form.addRow("", self.press_enter)

        self.after = QComboBox()
        self.after.addItem("Type into the text box", "type")
        self.after.addItem("Only copy to the clipboard", "clipboard")
        self.after.addItem("Copy, then paste into the focused window", "paste")
        self.after.setCurrentIndex(
            {"type": 0, "clipboard": 1, "paste": 2}.get(cfg.get("after_talking", "type"), 0))
        form.addRow("After talking", self.after)

        self.in_games = QCheckBox("Keep shortcuts on during VR games")
        self.in_games.setChecked(cfg.get("in_games", False))
        form.addRow("Buttons", self.in_games)

        self.autostart = QCheckBox("Start automatically with SteamVR")
        self.autostart.setChecked(cfg.get("autostart", True))
        form.addRow("", self.autostart)

        update_row = QHBoxLayout()
        self.update_btn = QPushButton("Check for updates")
        self.update_btn.clicked.connect(self._check_update)
        self.update_label = QLabel("")
        self.update_label.setObjectName("muted")
        update_row.addWidget(self.update_btn)
        update_row.addWidget(self.update_label, 1)
        form.addRow("Updates", update_row)

        self.ai_polish = QCheckBox("Tidy each dictation with AI (via your OpenRouter account)")
        self.ai_polish.setChecked(cfg.get("ai_polish", False))
        form.addRow("AI polish", self.ai_polish)

        self.ai_key = QLineEdit(cfg.get("ai_key", ""))
        self.ai_key.setPlaceholderText("sk-or-... (your own key; text only is sent)")
        form.addRow("OpenRouter key", self.ai_key)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _check_update(self):
        from .update import run_self_update
        self.update_btn.setEnabled(False)
        self.update_label.setText("Checking…")
        self.update_label.repaint()
        try:
            message = run_self_update()
        except Exception as err:
            message = f"Update failed: {err}"
        self.update_label.setText(message)
        self.update_btn.setEnabled(True)

    def values(self):
        return {
            "model": self.model.currentData(),
            "add_space": self.add_space.isChecked(),
            "press_enter": self.press_enter.isChecked(),
            "after_talking": self.after.currentData(),
            "in_games": self.in_games.isChecked(),
            "autostart": self.autostart.isChecked(),
            "ai_polish": self.ai_polish.isChecked(),
            "ai_key": self.ai_key.text().strip(),
        }


class MainWindow(QMainWindow):
    """The visible FrameAssist window."""

    # Signals from other threads to the UI thread.
    state_sig = Signal(str, str)
    transcript_sig = Signal(str)

    def __init__(self, cfg, engine, on_settings=None, on_quit=None, link=None,
                 on_turn_on=None, on_power=None):
        super().__init__()
        self.cfg = cfg
        self.engine = engine
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.link = link
        # Called when the user taps "Turn on" for the SteamVR global-input
        # setting. Set by app.py to vr.enable_global_input.
        self.on_turn_on = on_turn_on
        # Called with True/False when the user powers the app off/on from the
        # app menu (stop now + don't start with SteamVR, or the reverse). Set
        # by app.py to autostart.power. Returns (ok, summary).
        self.on_power = on_power
        # Set by app.py: a callable()-> overlay that renders calibration charts
        # in VR when SteamVR is available, else None.
        self.calibrate_overlay = None
        self.setWindowTitle("FrameAssist")
        self.setMinimumSize(360, 460)

        # App menu: turn FrameAssist fully off (stop now, don't start with
        # SteamVR) or back on, without editing system files by hand.
        self._build_app_menu()

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("FrameAssist")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        self.mic = MicButton()
        self.mic.setToolTip("Tap to talk (or hold both grips and talk)")
        if not hasattr(self, "mic_press"):
            self.mic.clicked.connect(self._mic_clicked)
        root.addWidget(self.mic, 0, Qt.AlignCenter)

        self.status_label = QLabel("Starting…")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status_label)

        self.turn_on_btn = QPushButton("Turn on (SteamVR setting)")
        self.turn_on_btn.setVisible(False)
        self.turn_on_btn.clicked.connect(self._turn_on)
        root.addWidget(self.turn_on_btn, 0, Qt.AlignCenter)

        self.last_label = QLabel("(hold both grips and talk — your words land here, "
                                 "and in the text box)")
        self.last_label.setObjectName("last")
        self.last_label.setWordWrap(True)
        self.last_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.last_label)

        row = QHBoxLayout()
        copy = QPushButton("Copy last dictation")
        copy.clicked.connect(self._copy_last)
        guide = QPushButton("How to use")
        guide.clicked.connect(self._show_guide)
        calibrate = QPushButton("Calibrate")
        calibrate.clicked.connect(self._open_calibrate)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(copy)
        row.addWidget(guide)
        row.addWidget(calibrate)
        row.addWidget(self.settings_btn)
        root.addLayout(row)

        # A clearly visible stop control so the user can turn FrameAssist fully
        # off before a game (or when not using it) without hunting in a menu.
        self.stop_btn = QPushButton("Stop FrameAssist (before a game)")
        self.stop_btn.setStyleSheet(
            f"QPushButton {{ background: {RED}; color: #fff; border: none; "
            f"border-radius: 10px; padding: 10px 16px; font-weight: 600; }} "
            f"QPushButton:hover {{ background: #ff6b78; }}")
        self.stop_btn.clicked.connect(self._power_off)
        root.addWidget(self.stop_btn)

        root.addStretch(1)
        footer = QLabel("Hold both grips to talk · Double-tap both for hands-free · "
                        "Flick right stick to send · Flick left for Escape")
        footer.setObjectName("muted")
        footer.setWordWrap(True)
        footer.setAlignment(Qt.AlignCenter)
        root.addWidget(footer)

        self.setCentralWidget(central)

        # UI never takes typed keys.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_X11NetWmWindowTypeDesktop)

        self.state_sig.connect(self._show_state)
        self.transcript_sig.connect(self._show_transcript)

        QTimer.singleShot(0, self._start)

    # Called by app.py (from any thread) to push state into the window.
    def notify_state(self, state, message):
        self.state_sig.emit(state, message)

    def notify_transcript(self, text):
        self.transcript_sig.emit(text)

    def _start(self):
        self._show_state(self.engine.state, self.engine.message)

    def _mic_clicked(self):
        if self.engine.state == LISTENING:
            self.engine.hotkey({"cmd": "talk_stop"})
        else:
            self.engine.hotkey({"cmd": "talk_start"})

    def _copy_last(self):
        if self.engine.last_text:
            QApplication.clipboard().setText(self.engine.last_text)
            self.status_label.setText("Copied. Paste it anywhere.")
            self.engine._set(READY, "Copied. Paste it anywhere.")

    def _show_guide(self):
        guide = QDialog(self)
        guide.setWindowTitle("How to use FrameAssist")
        guide.setMinimumWidth(460)
        layout = QVBoxLayout(guide)
        text = QLabel(
            "🎙️ <b>Type with your voice</b><br>"
            "Click a text box, hold <b>both grips</b>, talk, and let go — your words land there.<br><br>"
            "🎯 <b>Hands-free</b><br>"
            "Double-tap both grips, put the controllers down and talk (up to 10 minutes). "
            "Tap both grips once to stop.<br><br>"
            "📋 <b>Copy / paste</b><br>"
            "Double-tap the left grip to copy your last dictation; double-tap the right grip to paste.<br><br>"
            "⏎ <b>Send / Escape</b><br>"
            "Flick the right stick to send (Enter); flick the left stick for Escape.<br><br>"
            "🔒 <b>Private & offline</b><br>"
            "Speech runs on your headset. In games the buttons rest unless you turn on "
            "“Keep shortcuts on during VR games” in Settings.<br><br>"
            "Every dictation is also copied to the clipboard, so you can paste it anywhere."
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        layout.addWidget(text)
        close = QPushButton("Got it")
        close.clicked.connect(guide.accept)
        layout.addWidget(close, 0, Qt.AlignRight)
        guide.exec()

    def _open_settings(self):
        dialog = SettingsDialog(self.cfg, self)
        if dialog.exec():
            values = dialog.values()
            for key, value in values.items():
                self.cfg[key] = value
            try:
                save(self.cfg)
            except OSError:
                pass
            if self.on_settings:
                self.on_settings(self.cfg)
            self.status_label.setText("Settings saved.")

    def _build_app_menu(self):
        """The app menu: power the whole thing off (stop now + no auto-start)
        or back on. Useful when tuning game performance or just not using it."""
        bar = self.menuBar()
        menu = bar.addMenu("App")

        self.power_off_action = QAction("Turn off (stop now & don't start with SteamVR)", self)
        self.power_off_action.triggered.connect(self._power_off)
        menu.addAction(self.power_off_action)

        self.power_on_action = QAction("Turn on (start now & start with SteamVR)", self)
        self.power_on_action.triggered.connect(self._power_on)
        menu.addAction(self.power_on_action)

        menu.addSeparator()
        quit_action = QAction("Quit (window only, shortcuts keep running)", self)
        quit_action.triggered.connect(lambda: self.close())
        menu.addAction(quit_action)

        # Reflect the current state when the menu opens.
        menu.aboutToShow.connect(self._refresh_power_menu)

    def _refresh_power_menu(self):
        try:
            from . import autostart
            on = autostart.enabled()
        except Exception:
            on = self.cfg.get("autostart", True)
        self.power_off_action.setEnabled(on)
        self.power_on_action.setEnabled(not on)

    def _power_off(self):
        if not self.on_power:
            return
        reply = QMessageBox.question(
            self, "Turn off FrameAssist?",
            "Stop FrameAssist now and don't start it with SteamVR again?\n\n"
            "Run “frameassist --register” (or reopen Settings → Start automatically) "
            "to turn it back on.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        ok, summary = self.on_power(False)
        self.status_label.setText(summary)
        if ok:
            # Fully off: nothing is listening anymore, so close the window too.
            self.close()
        else:
            self.status_label.setText(summary)

    def _power_on(self):
        if not self.on_power:
            return
        ok, summary = self.on_power(True)
        self.status_label.setText(summary)
        self._refresh_power_menu()

    def _open_calibrate(self):
        dialog = CalibrateDialog(self.engine, self, overlay_factory=self.calibrate_overlay)
        dialog.exec()
        # engine.calibrate stays attached with whatever state the dialog left.
        self.status_label.setText(self.engine.calibrate.status() if self.engine.calibrate
                                  else "Calibration off.")

    def _show_state(self, state, message):
        self.mic.set_listening(state == LISTENING)
        color = STATE_COLORS.get(state, ACCENT)
        text = message or STATE_TEXT.get(state, "Ready")
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        # Show the "Turn on" shortcut when SteamVR needs its global-input
        # setting enabled (the message carries that exact wording).
        self.turn_on_btn.setVisible(
            bool(message) and ("Enable global input" in message or "Turn on" in message))

    def _turn_on(self):
        if self.on_turn_on:
            self.on_turn_on()
        self.status_label.setText("Turned on. Restart the headset to finish.")

    def _show_transcript(self, text):
        if text:
            self.last_label.setText(text)
            if self.link:
                self.link.send_last(text)
        else:
            self.last_label.setText("(didn't catch that — try again)")

    def closeEvent(self, event):
        if self.on_quit:
            self.on_quit()
        super().closeEvent(event)


class CalibrateDialog(QDialog):
    """Start/stop the on-headset calibration charts, see which one you're on,
    and save the current sweet spot as a named profile."""

    def __init__(self, engine, parent=None, overlay_factory=None):
        super().__init__(parent)
        self.engine = engine
        self.overlay_factory = overlay_factory or (lambda: None)
        self.overlay = None
        self.setWindowTitle("Calibrate FrameAssist")
        self.setMinimumWidth(480)

        from .calibrate import CalibrationSession, load_profiles, remove_profile, save_profile

        self.CalibrationSession = CalibrationSession
        self.load_profiles = load_profiles
        self.remove_profile = remove_profile
        self.save_profile = save_profile

        self.session = engine.calibrate if getattr(engine, "calibrate", None) else CalibrationSession()
        self.session.on_change = self._on_session_change
        engine.calibrate = self.session

        root = QVBoxLayout(self)
        hint = QLabel(
            "Roll the IPD wheel while a chart is shown. Flick right stick = next, "
            "left = back. Talk to choose: “finer”, “coarser”, “next”, "
            "“save as my fit”, “done”.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        root.addWidget(hint)

        self.status = QLabel(self.session.status())
        self.status.setObjectName("status")
        self.status.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
        root.addWidget(self.status)

        row = QHBoxLayout()
        self.start_btn = QPushButton("Start" if not self.session.active else "Stop")
        self.start_btn.clicked.connect(self._toggle)
        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(lambda: self._act(self.session.next))
        self.prev_btn = QPushButton("Back")
        self.prev_btn.clicked.connect(lambda: self._act(self.session.prev))
        row.addWidget(self.start_btn)
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        root.addLayout(row)

        finer = QPushButton("Finer")
        finer.clicked.connect(lambda: self._act(self.session.finer))
        coarser = QPushButton("Coarser")
        coarser.clicked.connect(lambda: self._act(self.session.coarser))
        row2 = QHBoxLayout()
        row2.addWidget(coarser)
        row2.addWidget(finer)
        root.addLayout(row2)

        self.profiles_label = QLabel("")
        self.profiles_label.setObjectName("muted")
        self.profiles_label.setWordWrap(True)
        root.addWidget(self.profiles_label)

        save_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Save current as (e.g. with-spacers)")
        self.save_btn = QPushButton("Save profile")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.name_edit, 1)
        save_row.addWidget(self.save_btn)
        root.addLayout(save_row)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        root.addWidget(close, 0, Qt.AlignRight)

        self._refresh_profiles()

    def _on_session_change(self, status, message=""):
        self.status.setText(status)
        if message:
            self.status.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
        active = bool(getattr(self.session, "active", False))
        self._update_overlay(active)
        self.start_btn.setText("Stop" if active else "Start")

    def _update_overlay(self, active):
        if not active:
            if self.overlay:
                self.overlay.close()
                self.overlay = None
            return
        if self.overlay is None:
            self.overlay = self.overlay_factory()
        if self.overlay is not None:
            self.overlay.show(self.session.test, self.session.fineness)

    def _toggle(self):
        if self.session.active:
            self._act(self.session.stop)
        else:
            self._act(self.session.start)

    def _act(self, method):
        method()
        self._on_session_change(self.session.status())

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            self.status.setText("Type a profile name first.")
            return
        self.save_profile(name, test=self.session.test, fineness=self.session.fineness)
        self.name_edit.clear()
        self.status.setText(f"Profile '{name}' saved.")
        self._refresh_profiles()

    def _refresh_profiles(self):
        profiles = self.load_profiles()
        if profiles:
            names = ", ".join(p.get("name", "?") for p in profiles)
            self.profiles_label.setText("Saved: " + names)
        else:
            self.profiles_label.setText("No profiles saved yet.")

    def reject(self):
        self._update_overlay(False)
        super().reject()

    def accept(self):
        self._update_overlay(False)
        super().accept()


def run_window(cfg, engine, on_settings=None, on_quit=None, link=None):
    """Run the FrameAssist window. Returns the app exit code."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _style(app)
    window = MainWindow(cfg, engine, on_settings=on_settings, on_quit=on_quit, link=link)
    window.show()
    return app.exec()

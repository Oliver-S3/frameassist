"""The FrameAssist window (PySide6).

A round mic button, a status line, the last dictation with a Copy button,
Settings (speech model, space/Enter, in-games, auto-start, AI polish), and
"My words". On KDE a window rule keeps our window from taking keyboard focus,
and the window ignores typed keys so it can never be typed into.
"""

import sys

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

from .config import MODELS, load, load_words, save, words_file
from .engine import ERROR, LISTENING, READY, WORKING
from . import __version__

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
    app.setApplicationName("FrameAssist")
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
        self.after.setCurrentIndex(0 if cfg.get("after_talking", "type") == "type" else 1)
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

    def __init__(self, cfg, engine, on_settings=None, on_quit=None, link=None):
        super().__init__()
        self.cfg = cfg
        self.engine = engine
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.link = link
        self.setWindowTitle("FrameAssist")
        self.setMinimumSize(360, 460)

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
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(copy)
        row.addWidget(guide)
        row.addWidget(self.settings_btn)
        root.addLayout(row)

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

    def _show_state(self, state, message):
        self.mic.set_listening(state == LISTENING)
        color = STATE_COLORS.get(state, ACCENT)
        text = message or STATE_TEXT.get(state, "Ready")
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

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


def run_window(cfg, engine, on_settings=None, on_quit=None, link=None):
    """Run the FrameAssist window. Returns the app exit code."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _style(app)
    window = MainWindow(cfg, engine, on_settings=on_settings, on_quit=on_quit, link=link)
    window.show()
    return app.exec()

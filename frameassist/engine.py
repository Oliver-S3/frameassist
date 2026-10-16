"""The engine: takes gesture commands and turns them into typed text.

It owns the recorder, the transcriber, post-processing and the typist, and
reports state (READY / LISTENING / WORKING / ERROR) with a short message for
the window and the VR badge.

``hotkey(bundle)`` is called by SteamVR with gesture commands:
{"cmd": "talk_start" | "talk_stop" | "handsfree_start" | "handsfree_stop" |
 "copy" | "paste" | "enter" | "escape"}.
"""

import threading

from .audio import Recorder
from .keyboard import Typist
from .log import log
from .postprocess import finalize, spell_with_words
from .voice import Transcriber

READY = "ready"
LISTENING = "listening"
WORKING = "working"
ERROR = "error"


class Engine:
    def __init__(self, cfg, on_state=None, on_transcript=None,
                 transcriber=None, typist=None, recorder=None, copy_clipboard=None):
        self.cfg = cfg
        self.on_state = on_state or (lambda state, message: None)
        # on_transcript(text): the final text, after post-processing (also the
        # "last dictation").
        self.on_transcript = on_transcript or (lambda text: None)
        self.copy_clipboard = copy_clipboard or (lambda text: None)

        self.state = READY
        self.message = ""
        self.last_text = ""
        self.paused = False  # a game is running; don't act on gestures
        self.handsfree = False

        self.transcriber = transcriber
        self._typist = typist
        self._recorder = recorder or Recorder()
        self._lock = threading.Lock()
        self._working = False
        # Calibration session (calibrate.py): when active, flicks and talk
        # drive the chart instead of typing text.
        self.calibrate = None

    # -- loading ---------------------------------------------------------------
    def load(self):
        """Load the speech model (can be slow; called on a worker thread)."""
        if self.transcriber is None and not self.cfg.get("no_model"):
            try:
                self.transcriber = Transcriber(self.cfg["model"])
            except Exception as err:
                log.exception("couldn't load the speech model")
                self._set(ERROR, f"Speech model failed to load: {err}")
                return
        self._set(READY, "Ready")

    def reload_model(self, model):
        try:
            self.transcriber = Transcriber(model)
        except Exception as err:
            log.exception("couldn't reload the model")
            self._set(ERROR, f"Model reload failed: {err}")

    # -- hotkey entry (called from the SteamVR thread) --------------------------
    def hotkey(self, bundle):
        cmd = bundle.get("cmd")
        if self.paused:
            return
        with self._lock:
            if cmd in ("talk_start", "handsfree_start"):
                if cmd == "handsfree_start":
                    self.handsfree = True
                self._start_talking()
            elif cmd in ("talk_stop", "handsfree_stop"):
                if cmd == "handsfree_stop":
                    self.handsfree = False
                self._stop_talking()
            elif cmd == "copy":
                if self.last_text:
                    self.copy_clipboard(self.last_text)
                    self._set(READY, "Copied. Paste it anywhere.")
            elif cmd == "paste":
                self._need_typist().shortcut("paste")
                self._set(READY, "Pasted.")
            elif cmd == "enter":
                if self.calibrate is not None and self.calibrate.active:
                    self.calibrate.next()
                else:
                    self._need_typist().shortcut("enter")
            elif cmd == "escape":
                if self.calibrate is not None and self.calibrate.active:
                    self.calibrate.prev()
                else:
                    self._need_typist().shortcut("escape")

    def cancel_talking(self):
        with self._lock:
            self.handsfree = False
            self._recorder.cancel()
            if self._working:
                return  # let the transcribe thread finish its current run
            self._set(READY, "")

    # -- talking ----------------------------------------------------------------
    def _start_talking(self):
        if self.state == LISTENING:
            return
        try:
            self._recorder.start()
        except Exception as err:
            log.exception("recorder failed")
            self._set(ERROR, f"Couldn't start recording: {err}")
            return
        self._set(LISTENING, "Listening…")

    def _stop_talking(self):
        if self.state != LISTENING:
            return
        path = self._recorder.stop()
        if not path:
            self._set(READY, "")
            return
        self._set(WORKING, "Typing…")
        threading.Thread(target=self._finish, args=(path,), daemon=True).start()

    def _finish(self, path):
        try:
            text = self._transcribe(path)
        except Exception as err:
            log.exception("transcribe failed")
            with self._lock:
                self._set(ERROR, f"Voice typing error: {err}")
            return
        finally:
            try:
                import os
                os.unlink(path)
            except OSError:
                pass
        if not text:
            with self._lock:
                self._set(READY, "Didn't catch that. Try again?")
            return
        with self._lock:
            self._finish_text(text)

    def _transcribe(self, path):
        if self.transcriber is None:
            raise RuntimeError("speech model is not loaded")
        words = ", ".join(self._words()) if self._words() else ""
        return self.transcriber.transcribe(path, words=words)

    def _words(self):
        from .config import load_words
        return load_words()

    def _finish_text(self, text):
        text = spell_with_words(text, self._words())
        text = finalize(text, self.cfg, polish=self)
        text = text.lstrip()  # keep the trailing "space after dictation"
        self.last_text = text.strip()

        # While calibrating, the words are a command to the chart, not text to
        # type into a text box.
        if self.calibrate is not None and self.calibrate.active:
            command, detail = self.calibrate.interpret(text)
            if command == "save" and detail:
                from .calibrate import save_profile
                cal = self.calibrate
                try:
                    save_profile(detail, test=cal.test, fineness=cal.fineness)
                    self._set(READY, f"Profile '{detail}' saved.")
                except OSError as err:
                    log.warning("couldn't save profile %r: %s", detail, err)
                    self._set(READY, f"Couldn't save profile: {err}")
            else:
                self._set(READY, self.calibrate.status())
            return

        if text.strip():
            self.copy_clipboard(text.strip())
            mode = self.cfg.get("after_talking", "type")
            if mode == "type":
                self._need_typist().type_text(text)
                if self.cfg.get("press_enter"):
                    self._need_typist().shortcut("enter")
            elif mode == "paste":
                # The text is already on the clipboard (set above). Send Ctrl+V
                # so it lands in whatever text box has focus on the Frame.
                # (Note: this follows the Frame's own focused window, like
                # typing does — it is not forwarded into apps streamed from a
                # PC, which Steam Remote Play/vrlink only delivers through
                # Steam's own on-screen keyboard.)
                import time as _t
                _t.sleep(0.35)
                self._need_typist().shortcut("paste")
            log.info("dictated: %r", text)
        self.on_transcript(text.strip())
        self._set(READY, "Ready")

    # -- polish hook (postprocess.finalize calls polish.apply) -------------------
    def apply(self, text):
        """AI polish: configured, returns cleaned text; else unchanged."""
        if self.cfg.get("ai_polish") and self.cfg.get("ai_key"):
            from .aipolish import polish
            return polish(text, self.cfg["ai_key"], self.cfg.get("ai_model", ""))
        return text

    # -- bits and pieces ---------------------------------------------------------
    def _need_typist(self):
        if self._typist is None:
            self._typist = Typist()
        return self._typist

    def _set(self, state, message):
        self.state = state
        self.message = message
        self.on_state(state, message)

    def close(self):
        try:
            self._recorder.cancel()
        except Exception:
            pass
        if self._typist is not None:
            try:
                self._typist.close()
            except Exception:
                pass

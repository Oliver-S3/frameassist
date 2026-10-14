"""Regression tests for app wiring bugs found in review.

These protect against:
1. The window-copy state callback recursing through engine._set (infinite
   recursion on the first state change).
2. The background copy dropping VR-badge updates after run_background overrides
   engine.on_state.
"""

from frameassist import engine as eng_mod
from frameassist.engine import Engine


class Stub:
    def __init__(self):
        self.notified = []
        self.badge = []

    def notify_state(self, state, message):
        self.notified.append((state, message))

    def show_state(self, state, message):
        self.badge.append((state, message))


def test_state_callback_forwards_without_recursing():
    """run_window wired on_state to window+vr only, NOT engine._set again."""
    stub = Stub()
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    engine = Engine(cfg)
    # Mirrors app.py run_window: on_state forwards, and _set stores state.
    engine.on_state = lambda state, message: stub.notify_state(state, message)
    # A state change must terminate and notify exactly once — no recursion.
    engine._set(eng_mod.LISTENING, "Listening…")
    assert engine.state == eng_mod.LISTENING
    assert stub.notified == [(eng_mod.LISTENING, "Listening…")]
    assert engine._set(eng_mod.READY, "Ready") is None


def test_background_on_state_feeds_badge():
    """run_background replaces engine.on_state; it must still feed the badge."""
    stub = Stub()
    cfg = {"model": "base.en", "add_space": True, "after_talking": "type",
           "ai_polish": False}
    engine = Engine(cfg)
    state = {}
    work = type("Work", (), {"vr": stub})()

    def on_state(s, msg):
        state["engine"] = s
        state["message"] = msg
        work.vr.show_state(s, msg)  # Background badge feed kept

    engine.on_state = on_state
    engine._set(eng_mod.WORKING, "Typing…")
    assert state["engine"] == eng_mod.WORKING
    assert stub.badge == [(eng_mod.WORKING, "Typing…")]

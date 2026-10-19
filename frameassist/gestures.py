"""The FrameAssist gesture engine.

Pure logic: it turns raw controller button events (press/release with a
timestamp) into the handful of commands the rest of the app understands. It
has no idea what a controller or SteamVR is, so it can be tested with plain
timestamps.

Gestures (matching FrameAssist on the Steam Frame):

* Hold both grips  -> talk while they're held. Let go -> type.
* Double-tap both grips -> hands-free dictation (up to 10 minutes by default).
  Tap both grips once (a single chord tap) to stop early.
* Double-tap the LEFT grip only  -> copy.
* Double-tap the RIGHT grip only -> paste.
* Quickly press (click) the right stick  -> Enter ("send").
* Quickly press (click) the left stick   -> Escape.
* Hold one grip for a long time does nothing (a mistaken press or a grab).

A "tap" is a press+release shorter than ``tap_max``. Two taps of the same
kind within ``double_ms`` make a double-tap. A chord is both grips being down
at the same time (a small press skew is tolerated). Once a chord breaks, the
second grip's release is a "trail" and is ignored, so a chord can never be
counted as a lone single-side tap.

    engine = GestureEngine(on_command)
    engine.press("grip", "left",  t)
    engine.release("grip", "left", t)
    engine.press("stick", "right", t)
    ...
    engine.tick(t)   # drive timers each frame (hold-to-talk, hands-free max)

Commands delivered through ``on_command``:
"talk_start" / "talk_stop" (hold or hands-free start/stop),
"handsfree_start" / "handsfree_stop", "copy", "paste", "enter", "escape".
"""

# Command names.
TALK_START = "talk_start"
TALK_STOP = "talk_stop"
HANDSFREE_START = "handsfree_start"
HANDSFREE_STOP = "handsfree_stop"
COPY = "copy"
PASTE = "paste"
ENTER = "enter"
ESCAPE = "escape"

GRIP = "grip"
STICK = "stick"
LEFT = "left"
RIGHT = "right"

DEFAULT_TIMINGS = {
    "tap_max": 0.30,             # a press shorter than this is a tap
    "double_ms": 0.45,           # two taps within this are a double-tap
    "talk_hold_min": 0.22,       # hold both grips this long to start talking
    "chord_tap_follow": 0.15,    # 2nd grip must come up within this of the 1st
                                 # release to count as a chord-tap (both tapped)
    "flick_max": 0.25,           # a stick release within this is a flick
    "handsfree_max": 600.0,      # 10 minutes of hands-free, then stop
}


class GestureEngine:
    def __init__(self, on_command=None, timings=None):
        self.on_command = on_command or (lambda cmd: None)
        t = dict(DEFAULT_TIMINGS)
        if timings:
            t.update(timings)
        self.timings = t

        self._down = {GRIP: {LEFT: False, RIGHT: False},
                      STICK: {LEFT: False, RIGHT: False}}
        self._pressed_at = {}       # (kind, side) -> start time
        self._chord_started = None  # time both grips were first down together
        self._trail = set()         # sides whose release is part of a broken chord
        self._pending_chord_tap = None  # (time, other_side): first grip of a
                                        # short chord released; waiting to see if
                                        # the other grip comes up too (chord-tap)
                                        # or lingers (a hold, so do nothing)
        self._talking_hold = False
        self._handsfree = False
        self._handsfree_since = 0.0
        self._last_chord_tap = None
        self._last_left_tap = None
        self._last_right_tap = None
        self._stick_press = {}

    # -- state queries ---------------------------------------------------------
    @property
    def talking(self):
        return self._talking_hold or self._handsfree

    @property
    def handsfree(self):
        return self._handsfree

    def _grip_pair_down(self):
        return all(self._down[GRIP][s] for s in (LEFT, RIGHT))

    # -- events ----------------------------------------------------------------
    def press(self, kind, side, t):
        self._down[kind][side] = True
        self._pressed_at[(kind, side)] = t
        if kind == STICK:
            self._stick_press[side] = t
            return
        # grips: when both are down, the chord has started
        if self._grip_pair_down() and self._chord_started is None:
            other = RIGHT if side == LEFT else LEFT
            self._chord_started = min(
                self._pressed_at.get((GRIP, side), t),
                self._pressed_at.get((GRIP, other), t),
            )
        self._maybe_talk(t)

    def release(self, kind, side, t):
        self._down[kind][side] = False
        started = self._pressed_at.pop((kind, side), None)
        if kind == STICK:
            press = self._stick_press.pop(side, None)
            if press is not None and started is not None:
                if t - press <= self.timings["flick_max"]:
                    self._emit(ENTER if side == RIGHT else ESCAPE)
            return

        # grips
        other = RIGHT if side == LEFT else LEFT
        other_down = self._down[GRIP][other]

        if self._chord_started is not None:
            # The first grip to leave a live chord.
            duration = t - self._chord_started
            self._chord_started = None
            if self._talking_hold:
                self._talking_hold = False
                self._emit(TALK_STOP)
                self._clear_tap_windows()
            elif other_down and duration <= self.timings["tap_max"]:
                # Both grips were briefly down. Don't call this a chord-tap yet:
                # a hands-free double-tap releases both grips together, while a
                # hold (that just missed talk_hold_min) leaves the other grip
                # down. Defer the decision until the other grip comes up.
                self._pending_chord_tap = (t, other)
                self._trail.add(other)
            else:
                self._clear_tap_windows()
                if other_down:
                    self._trail.add(other)
            return

        # Not a live chord: a lone press, the tail of a broken one, or the
        # second half of a deferred chord-tap.
        if self._pending_chord_tap is not None:
            pend_t, pend_other = self._pending_chord_tap
            self._pending_chord_tap = None
            if side == pend_other and t - pend_t <= self.timings["chord_tap_follow"]:
                self._trail.discard(side)
                self._chord_tap(pend_t)
                return
            # The other grip lingered — this was a hold, not a tap. Do nothing.
            self._clear_tap_windows()
            return
        if side in self._trail:
            self._trail.discard(side)
            return
        if started is not None and t - started <= self.timings["tap_max"]:
            self._single_tap(side, t)
        else:
            self._clear_tap_windows()

    def tick(self, t):
        """Called each frame: drive the hold-to-talk and hands-free timers."""
        self._maybe_talk(t)
        if self._handsfree and self._handsfree_since:
            if t - self._handsfree_since >= self.timings["handsfree_max"]:
                self._handsfree = False
                self._handsfree_since = 0.0
                self._emit(TALK_STOP)
        # A deferred chord-tap that never got confirmed (the other grip stayed
        # down) is a hold/aborted tap, not a gesture — let it expire.
        if self._pending_chord_tap is not None:
            pend_t, _ = self._pending_chord_tap
            if t - pend_t > self.timings["chord_tap_follow"]:
                self._pending_chord_tap = None
                self._clear_tap_windows()
        # expire old tap windows
        for attr in ("_last_left_tap", "_last_right_tap", "_last_chord_tap"):
            last = getattr(self, attr)
            if last is not None and t - last > self.timings["double_ms"]:
                setattr(self, attr, None)

    # -- internals -------------------------------------------------------------
    def _maybe_talk(self, t):
        if self._handsfree or self._talking_hold:
            return
        if self._chord_started is None:
            return
        if t - self._chord_started >= self.timings["talk_hold_min"]:
            self._talking_hold = True
            self._emit(TALK_START)

    def _chord_tap(self, t):
        # A chord tap: both grips tapped together.
        if self._handsfree:
            # One tap stops hands-free.
            self._handsfree = False
            self._handsfree_since = 0.0
            self._emit(HANDSFREE_STOP)
            self._last_chord_tap = None
            return
        last = self._last_chord_tap
        if last is not None and t - last <= self.timings["double_ms"]:
            self._handsfree = True
            self._handsfree_since = t
            self._emit(HANDSFREE_START)
            self._last_chord_tap = None
        else:
            self._last_chord_tap = t
        self._last_left_tap = self._last_right_tap = None

    def _single_tap(self, side, t):
        attr = "_last_left_tap" if side == LEFT else "_last_right_tap"
        other = "_last_right_tap" if side == LEFT else "_last_left_tap"
        last = getattr(self, attr)
        if last is not None and t - last <= self.timings["double_ms"]:
            self._emit(COPY if side == LEFT else PASTE)
            setattr(self, attr, None)
        else:
            setattr(self, attr, t)
        setattr(self, other, None)
        self._last_chord_tap = None

    def _clear_tap_windows(self):
        self._last_left_tap = self._last_right_tap = self._last_chord_tap = None

    def _emit(self, command):
        self.on_command(command)

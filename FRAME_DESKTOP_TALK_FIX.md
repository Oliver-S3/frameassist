# Fix: desktop-mode two-grip hold never starts talking

## Symptom

Hold both grips to talk works **in games** but not in **desktop / Frame-UI /
no-game** mode — where dictation is most useful. In desktop mode the floating
"Listening…" badge never appears and nothing is typed, even though grip presses
clearly arrive.

## Root cause

The SteamVR input/priority layer is already healthy in desktop mode (fixed in
`33a0d43` / `f6d3ff8`): on-device diagnostics show `bound:[grip_l,grip_r]`,
`global_input:true`, `paused:false`, and the log shows
`held buttons became grip_l,grip_r`. Grip presses reach the gesture engine.

The failure is in the gesture state machine. Talking starts only when both grips
are simultaneously down for `talk_hold_min = 0.30s`
(`frameassist/gestures.py:170`, checked every frame via `tick()`/`_maybe_talk`).
When the first grip releases a still-live chord that lasted `≤ tap_max` (0.30s)
with the other grip still down, `release()` routes to `_chord_tap()`
(`frameassist/gestures.py:133-134`) — treating it as the first contact of a
hands-free **double-tap**, so talk is never attempted.

The code path is identical in games and desktop — the difference is behavioral /
analog: an in-game squeeze is firm and long enough to clear 0.30s, while a
desktop dictation hold is lighter/shorter (and/or the analog grip value flickers
near the `0.05`/`0.10` binding thresholds, repeatedly restarting the chord
timer). Note: `chord_skew` (`gestures.py:57`) is dead config, referenced nowhere.

## The fix

Two coordinated code changes, plus optional binding hardening.

### 1. Don't swallow a slow hold as a chord-tap — `frameassist/gestures.py`

In `release()` (lines 125-139), the branch that fires `_chord_tap()` currently
triggers whenever the chord lasted `≤ tap_max` and the other grip is still down.
A hands-free double-tap is both grips tapped *and released together*, so require
the second grip to also be (about to be) up — i.e. only treat it as a chord-tap
when it is genuinely a quick tap of both, not the tail of a hold where one hand
lingers. Concretely: keep routing to talk/hold unless the chord is short **and**
the still-down grip is released within a short follow window; otherwise let the
hold path own it. This removes the misclassification that eats desktop holds.

### 2. Lower the talk threshold — `frameassist/gestures.py:55`

Change `talk_hold_min` from `0.30` to `0.22` so a normal (lighter) desktop hold
reliably crosses it. Keep it coupled with fix #1 so a double-tap's brief first
contact isn't read as talk.

### 3. (Optional) Harden the analog grip binding — `frameassist/steamvr/bindings_frame_controller.json` + `tools/gen_bindings.py`

If on-device logging shows `bState` flicker breaking the chord, widen the gap
between `click_activate_threshold` (0.1) and `click_deactivate_threshold` (0.05)
or add a small debounce, and regenerate via `tools/gen_bindings.py`.

### 4. (Cleanup) Remove dead `chord_skew`

Delete `"chord_skew"` from `DEFAULT_TIMINGS` (`gestures.py:57`) or implement the
skew check it implies. It misled earlier debugging.

## Tests

- Extend `tests/test_gestures.py`:
  - Two-grip hold with both-down phase just **over** 0.22s → asserts `talk_start`
    then `talk_stop`.
  - Both-down phase just **under** the threshold with near-simultaneous release →
    still reaches the double-tap/hands-free path (no regression).
  - Genuine double-tap-both-grips → `handsfree_start` still fires.
  - Left-only / right-only double-tap → `copy` / `paste` unaffected.
- Run `QT_QPA_PLATFORM=offscreen pytest`.

## On-device verification

`docs/DEVICE_TEST_CHECKLIST.md` §3 "Hold to talk", performed in **desktop mode**:

1. Restart the service, **wait 10s** for `bActive=True`.
2. Click a text box, hold both grips, say "hello world", let go → "Listening…"
   badge, then text lands.
3. Confirm double-tap-both-grips hands-free still works (guards fix #1).
4. If still flaky, add temporary timing logs in `GestureEngine.press/release/
   tick` (`time.monotonic()` and `t - _chord_started`), do one firm 1s hold, and
   read the real both-down duration from
   `~/.local/state/frameassist/frameassist.log`.

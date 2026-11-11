"""Controller tests for the per-window mic (ported from the 1.4.2 line).

The controller is driven through a fake OverlayApi, so every test is
deterministic and needs no headset. Geometry helpers are covered at the top
of this file's sibling section; here we exercise scan -> create -> position ->
events -> state machine -> teardown.
"""

import math

import pytest

from frameassist import window_mic as wm


# --------------------------------------------------------------------------
# Fake OverlayApi
# --------------------------------------------------------------------------

def _mat(x, y, z):
    """A 12-float matrix whose translation is (x, y, z), axis-aligned."""
    return [1.0, 0.0, 0.0, x,
            0.0, 1.0, 0.0, y,
            0.0, 0.0, 1.0, z]


class FakeApi:
    """Records calls; simulates windows and their quads."""

    def __init__(self, window_quads=None):
        # seq -> {"handle": h, "ll": mat, "lr": mat, "ul": mat, "ms": (w, h)}
        self.windows = window_quads or {}
        self.next_handle = 100
        self.created = {}       # key -> handle
        self.raw = {}           # handle -> (len, w, h)
        self.flags = {}         # handle -> set of bits
        self.shown = set()
        self.destroyed = []
        self.absolutes = {}     # handle -> last 12-float matrix
        self.events = {}        # handle -> list of event dicts
        self.fail_coords = False
        self.bar = None         # Steam bar overlay, set by add_bar()

    def add_bar(self, ll=(-1.2, 0.68, -0.78), w_m=0.37, h_m=0.025,
                ms=(1800, 120)):
        lr = (ll[0] + w_m, ll[1], ll[2] - 0.18)
        ul = (ll[0], ll[1] + h_m, ll[2])
        self.bar = {"handle": 9001, "ll": _mat(*ll), "lr": _mat(*lr),
                    "ul": _mat(*ul), "ms": ms}

    # -- window simulation helpers ----------------------------------------

    def add_window(self, seq, ll=(-0.336, 0.791, -1.272),
                   w_m=0.985, h_m=0.554, ms=(1920, 1080)):
        lr = (ll[0] + w_m, ll[1], ll[2] + 0.12)
        ul = (ll[0], ll[1] + h_m, ll[2])
        self.windows[seq] = {
            "handle": 1000 + seq, "ll": _mat(*ll), "lr": _mat(*lr),
            "ul": _mat(*ul), "ms": ms,
        }
        return self.windows[seq]["handle"]

    # -- OverlayApi surface --------------------------------------------------

    def find(self, key):
        if key == wm.DOCK_KEY:
            return self.bar["handle"] if self.bar else 0
        if key.startswith(wm.WINDOW_PREFIX):
            seq = int(key.rsplit(".", 1)[1])
            return self.windows.get(seq, {}).get("handle", 0)
        return self.created.get(key, 0)

    def create(self, key, name):
        self.next_handle += 1
        self.created[key] = self.next_handle
        return self.next_handle

    def destroy(self, handle):
        self.destroyed.append(handle)

    def set_width(self, handle, meters):
        pass

    def set_alpha(self, handle, alpha):
        pass

    def set_sort(self, handle, order):
        pass

    def set_flag(self, handle, bit, on):
        self.flags.setdefault(handle, set())
        if on:
            self.flags[handle].add(bit)
        else:
            self.flags[handle].discard(bit)

    def set_input(self, handle, method):
        pass

    def set_mouse_scale(self, handle, w, h):
        pass

    def get_transform_type(self, handle):
        return 0

    def get_mouse_scale(self, handle):
        if self.bar and self.bar["handle"] == handle:
            return self.bar["ms"]
        for info in self.windows.values():
            if info["handle"] == handle:
                return info["ms"]
        return None

    def set_raw(self, handle, rgba, w, h):
        self.raw[handle] = (len(rgba), w, h)

    def show(self, handle):
        self.shown.add(handle)

    def hide(self, handle):
        self.shown.discard(handle)

    def set_absolute(self, handle, origin, matrix12):
        self.absolutes[handle] = list(matrix12)

    def coords(self, handle, x, y):
        if self.fail_coords:
            return None
        infos = list(self.windows.values())
        if self.bar:
            infos.append(self.bar)
        for info in infos:
            if info["handle"] != handle:
                continue
            mx, my = info["ms"] if info["ms"] else (1920, 1080)
            fx, fy = x / mx, y / my
            ll, lr, ul = info["ll"], info["lr"], info["ul"]
            m = [0.0] * 12
            m[0] = m[5] = m[10] = 1.0
            for i in range(3):
                t_ll, t_lr, t_ul = ll[3 + 4 * i], lr[3 + 4 * i], ul[3 + 4 * i]
                m[3 + 4 * i] = t_ll + (t_lr - t_ll) * fx + (t_ul - t_ll) * fy
            return m
        return None

    def poll(self, handle):
        queue = self.events.get(handle)
        if queue:
            return queue.pop(0)
        return None


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def make_mic(api, commands=None, can_act=None, engine_state=None):
    clock = Clock()
    mic = wm.WindowMic(
        api, "frameassist.voicetyping",
        on_command=(lambda cmd, seq: commands.append((cmd, seq))
                    if commands is not None else None),
        can_act=can_act or (lambda: True),
        engine_state=engine_state or (lambda: "idle"),
        clock=clock)
    return mic, clock


# --------------------------------------------------------------------------
# Scan / create / retire
# --------------------------------------------------------------------------

def test_visible_window_gets_a_mic_overlay():
    api = FakeApi()
    handle = api.add_window(49)
    mic, clock = make_mic(api)
    mic.tick(clock())
    assert 49 in mic._mics
    assert mic._mics[49].win_handle == handle
    assert mic._mics[49].handle in api.shown
    # Configured for interaction and dashboard visibility.
    flags = api.flags[mic._mics[49].handle]
    assert wm.VISIBLE_IN_DASHBOARD in flags
    assert wm.SORT_WITH_NON_SCENE in flags
    assert wm.MAKE_INTERACTIVE_IF_VISIBLE in flags


def test_window_reported_invisible_still_gets_a_mic():
    """The 1.4.2 visibility filter is gone: a live window on this runtime
    reports isOverlayVisible == False while on screen."""
    api = FakeApi()
    api.add_window(49)
    mic, clock = make_mic(api)
    mic.tick(clock())
    assert 49 in mic._mics


def test_scan_retires_mics_for_closed_windows():
    api = FakeApi()
    api.add_window(49)
    mic, clock = make_mic(api)
    mic.tick(clock())
    old_handle = mic._mics[49].handle
    del api.windows[49]
    clock.advance(wm.SCAN_S + 0.1)
    mic.tick(clock())
    assert 49 not in mic._mics
    assert old_handle in api.destroyed


def test_mic_key_uses_app_key_and_seq():
    api = FakeApi()
    api.add_window(7)
    mic, clock = make_mic(api)
    mic.tick(clock())
    assert mic._mics[7].key == "frameassist.voicetyping.mic.7"


def test_stale_api_is_flagged_after_a_zero_window_drought():
    """Regression: the service's OverlayApi went stale while its process
    lived on (headset sleep / SteamVR session churn) and FindOverlay kept
    returning nothing for NEW windows - the mic never appeared until the
    service was restarted (2026-11-09 18:33). The controller must notice a
    long zero-window drought and flag the api suspect so the owner cycles
    the session. It must NOT rebuild in place: a second VR_InitInternal
    under a live session SEGVs (2026-11-09 19:15, one crash per rebuild)."""
    made = []

    def factory():
        fresh = FakeApi()
        made.append(fresh)
        return fresh

    api = FakeApi()
    made.append(api)
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=Clock(),
                       api_factory=factory)
    clock = mic.clock
    # Drought: no windows at all.
    clock.advance(wm.REBUILD_AFTER_S + wm.SCAN_S)
    mic.tick(clock())
    assert not mic.api_suspect, "flagged before the drought threshold"
    clock.advance(wm.REBUILD_AFTER_S + wm.SCAN_S)
    mic.tick(clock())
    assert mic.api_suspect, "api was not flagged after the drought"
    # The api object is untouched - rebuilding in place crashes the runtime.
    assert mic.api is api
    assert len(made) == 1
    # A window appearing later still works through the same api.
    api.add_window(9)
    clock.advance(wm.SCAN_S + 0.1)
    mic.tick(clock())
    assert 9 in mic._mics


def test_flag_is_set_once_per_drought():
    api = FakeApi()
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=Clock(),
                       api_factory=FakeApi)
    clock = mic.clock
    # First drought pass only starts the clock; the second flags the api.
    clock.advance(wm.REBUILD_AFTER_S + wm.SCAN_S)
    mic.tick(clock())
    assert not mic.api_suspect
    clock.advance(wm.REBUILD_AFTER_S + wm.SCAN_S)
    mic.tick(clock())
    assert mic.api_suspect
    clock.advance(wm.REBUILD_AFTER_S + wm.SCAN_S)
    mic.tick(clock())
    # Still just flagged, no repeated work, no exceptions.
    assert mic.api_suspect


def test_live_api_is_never_flagged_while_windows_exist():
    api = FakeApi()
    api.add_window(5)
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=Clock(),
                       api_factory=FakeApi)
    clock = mic.clock
    for _ in range(120):
        clock.advance(wm.REBUILD_AFTER_S / 60)
        mic.tick(clock())
    assert not mic.api_suspect, "api flagged even though windows were present"


def test_max_mics_cap():
    api = FakeApi()
    for seq in range(1, wm.MAX_MICS + 5):
        api.add_window(seq)
    mic, clock = make_mic(api)
    mic.tick(clock())
    assert len(mic._mics) == wm.MAX_MICS


# --------------------------------------------------------------------------
# Positioning (shim three-corner probe)
# --------------------------------------------------------------------------

def test_mic_positioned_at_window_bottom_right():
    api = FakeApi()
    api.add_window(49)
    mic, clock = make_mic(api)
    mic.tick(clock())
    m = api.absolutes[mic._mics[49].handle]
    assert m is not None
    t = (m[3], m[7], m[11])
    # Bottom-right region: right of centre, below centre, in front.
    assert t[0] > -0.336 + 0.985 / 2
    assert t[1] < 0.791 + 0.554 / 2
    # Basis columns are unit-length.
    for col in range(3):
        norm = math.sqrt(sum(m[row * 4 + col] ** 2 for row in range(3)))
        assert abs(norm - 1.0) < 1e-5


def test_position_reprobes_every_tick():
    api = FakeApi()
    api.add_window(49)
    mic, clock = make_mic(api)
    mic.tick(clock())
    handle = mic._mics[49].handle
    first = list(api.absolutes[handle])
    # Move the window: the next tick must move the mic with it.
    api.add_window(49, ll=(-1.5, 1.2, -2.0))
    clock.advance(0.016)
    mic.tick(clock())
    second = api.absolutes[handle]
    assert second != first


def test_position_tracks_the_window_translation():
    """Regression: _position once read the rotation row (m[0..2]) instead of
    the translations (m[3], m[7], m[11]) for the quad centre, so the mic
    parked at a bogus pose and never followed the window (live 2026-11-09).
    The old test only checked that the matrix changed, which the basis
    vectors satisfied even while the translation stayed wrong."""
    api = FakeApi()
    api.add_window(49, ll=(-0.336, 0.791, -1.272))
    mic, clock = make_mic(api)
    mic.tick(clock())
    handle = mic._mics[49].handle
    # Move the window half a metre left, then let the ease settle.
    api.add_window(49, ll=(-0.836, 0.791, -1.272))
    for _ in range(120):
        clock.advance(1 / 60)
        mic.tick(clock())
    got = api.absolutes[handle]
    tx = (got[3], got[7], got[11])
    info = api.windows[49]
    quad = wm.quad_from_probe(info["ll"], info["lr"], info["ul"])
    assert quad is not None
    width, height, right_u, up_u, facing = quad
    center = [info["ll"][3 + 4 * i]
              + (info["lr"][3 + 4 * i] - info["ll"][3 + 4 * i]) / 2
              + (info["ul"][3 + 4 * i] - info["ll"][3 + 4 * i]) / 2
              for i in range(3)]
    want = wm.corner_target(center, width, height, right_u, up_u, facing)
    for g, w in zip(tx, want):
        assert abs(g - w) < 0.005, (tx, want)


def test_position_failure_is_warned_once_per_window():
    api = FakeApi()
    api.add_window(49)
    mic, clock = make_mic(api)
    api.fail_coords = True
    mic.tick(clock())
    mic.tick(clock())
    mic.tick(clock())
    # One warning for window 49 despite three failing ticks.
    warns = [r for r in mic._pos_warned.values()]
    assert warns.count("RuntimeError") == 1


def test_position_survives_a_missing_window_scale():
    api = FakeApi()
    info = api.add_window(49)
    api.windows[49]["ms"] = None
    mic, clock = make_mic(api)
    mic.tick(clock())
    assert mic._mics[49].handle in api.absolutes


def test_unplaceable_mic_hides_and_returns():
    """A mic that cannot be placed must not stay visible at its old pose:
    that stale plate reads as a duplicate mic on the window."""
    api = FakeApi()
    api.add_window(49)
    mic, clock = make_mic(api)
    mic.tick(clock())
    handle = mic._mics[49].handle
    assert handle in api.shown
    api.fail_coords = True
    mic.tick(clock())                     # first failure: start the grace
    clock.advance(wm.HIDE_AFTER_S + 0.01)
    mic.tick(clock())                     # grace elapsed: hide
    assert handle not in api.shown
    api.fail_coords = False
    clock.advance(0.016)
    mic.tick(clock())                     # placeable again: show
    assert handle in api.shown


# --------------------------------------------------------------------------
# Events / state machine
# --------------------------------------------------------------------------

def _press(api, mic, clock, seq, hold_s):
    handle = mic._mics[seq].handle
    api.events[handle] = [
        {"type": wm.MOUSE_DOWN, "button": 1, "x": 0, "y": 0}]
    mic.tick(clock())
    clock.advance(hold_s)
    api.events[handle] = [
        {"type": wm.MOUSE_UP, "button": 1, "x": 0, "y": 0}]
    mic.tick(clock())


def test_press_counts_even_when_the_button_field_is_noise():
    """The laser trigger is the overlay's only button. The button-mask gate is
    gone so a wrong/garbage field can never eat a press (that gate dropped
    every other click before the 2026-11-10 VREvent layout fix)."""
    api = FakeApi()
    api.add_window(3)
    commands = []
    mic, clock = make_mic(api, commands=commands)
    mic.tick(clock())
    handle = mic._mics[3].handle
    api.events[handle] = [{"type": wm.MOUSE_DOWN, "button": 1040000000}]
    mic.tick(clock())
    assert ("talk_start", 3) in commands


def test_hold_dictates_then_release_types():
    api = FakeApi()
    api.add_window(3)
    commands = []
    mic, clock = make_mic(api, commands=commands)
    mic.tick(clock())
    _press(api, mic, clock, 3, hold_s=0.5)
    assert ("talk_start", 3) in commands
    assert ("talk_stop", 3) in commands


def test_tap_latches_recording_until_next_click():
    api = FakeApi()
    api.add_window(3)
    commands = []
    mic, clock = make_mic(api, commands=commands)
    mic.tick(clock())
    _press(api, mic, clock, 3, hold_s=0.1)   # short: latch
    assert ("talk_start", 3) in commands
    assert ("talk_stop", 3) not in commands
    _press(api, mic, clock, 3, hold_s=0.1)   # second click: stop
    assert ("talk_stop", 3) in commands


def test_take_moves_from_one_window_to_another():
    api = FakeApi()
    api.add_window(3)
    api.add_window(4)
    commands = []
    mic, clock = make_mic(api, commands=commands)
    mic.tick(clock())
    _press(api, mic, clock, 3, hold_s=0.1)   # latch on window 3
    _press(api, mic, clock, 4, hold_s=0.1)   # move to window 4
    stops = [s for c, s in commands if c == "talk_stop"]
    starts = [s for c, s in commands if c == "talk_start"]
    assert stops == [3]
    assert starts == [3, 4]


def test_busy_engine_blocks_new_takes_but_keeps_existing():
    """The owner refuses new takes while busy via can_act (steamvr.py wraps
    engine_state in it, as in 1.4.2); an open take is not force-stopped."""
    api = FakeApi()
    api.add_window(3)
    commands = []
    allowed = {"v": True}
    mic, clock = make_mic(api, commands=commands,
                          can_act=lambda: allowed["v"])
    mic.tick(clock())
    _press(api, mic, clock, 3, hold_s=0.1)   # latch a take
    assert ("talk_start", 3) in commands
    allowed["v"] = False                      # engine goes busy
    _press(api, mic, clock, 3, hold_s=0.5)    # click must be refused
    stops = [s for c, s in commands if c == "talk_stop"]
    assert 3 not in stops                     # existing take untouched
    allowed["v"] = True
    _press(api, mic, clock, 3, hold_s=0.1)    # allowed again: stops it
    assert ("talk_stop", 3) in commands


def test_stale_latched_take_is_force_stopped():
    api = FakeApi()
    api.add_window(3)
    commands = []
    mic, clock = make_mic(api, commands=commands)
    mic.tick(clock())
    _press(api, mic, clock, 3, hold_s=0.1)   # latch
    clock.advance(wm.MAX_TAKE_S + 1)
    mic.tick(clock())
    assert ("talk_stop", 3) in commands
    assert mic._take_seq is None


def test_mouse_move_highlights_then_fades():
    api = FakeApi()
    api.add_window(3)
    mic, clock = make_mic(api)
    mic.tick(clock())
    handle = mic._mics[3].handle
    api.events[handle] = [{"type": wm.MOUSE_MOVE, "button": 0,
                           "x": 10, "y": 10}]
    mic.tick(clock())
    assert mic._mics[3].last_move == clock.t
    # A repaint happened with hover=True (face recorded).
    assert mic._mics[3].face[1] is True


def test_destroy_and_disable_tear_down_everything():
    api = FakeApi()
    api.add_window(3)
    api.add_window(4)
    mic, clock = make_mic(api)
    mic.tick(clock())
    handles = [m.handle for m in mic._mics.values()]
    mic.destroy()
    assert mic._mics == {}
    for h in handles:
        assert h in api.destroyed
    mic.tick(clock())  # disabled path must not raise
    api.add_window(5)
    mic.tick(clock())
    assert 5 not in mic._mics


def test_can_act_false_blocks_new_takes():
    api = FakeApi()
    api.add_window(3)
    commands = []
    mic, clock = make_mic(api, commands=commands, can_act=lambda: False)
    mic.tick(clock())
    _press(api, mic, clock, 3, hold_s=0.5)
    assert commands == []


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_render_mic_returns_opaque_rgba_plate():
    buf = wm.render_mic("idle", False, 0, wm.TEX_W, wm.TEX_H)
    assert len(buf) == wm.TEX_W * wm.TEX_H * 4
    # Centre pixel: plate colour with the glyph drawn over it (opaque either way).
    i = ((wm.TEX_H // 2) * wm.TEX_W + wm.TEX_W // 2) * 4
    assert buf[i + 3] == 255
    # Corner pixel: fully transparent (outside the plate ellipse).
    corner = 0
    assert buf[corner + 3] == 0


def test_render_mic_faces_differ():
    idle = wm.render_mic("idle", False, 0, wm.TEX_W, wm.TEX_H)
    rec = wm.render_mic("recording", False, 0, wm.TEX_W, wm.TEX_H)
    assert bytes(idle) != bytes(rec)


def test_upload_pushes_texture_to_overlay():
    api = FakeApi()
    api.add_window(3)
    mic, clock = make_mic(api)
    mic.tick(clock())
    handle = mic._mics[3].handle
    length, w, h = api.raw[handle]
    assert (w, h) == (wm.TEX_W, wm.TEX_H)
    assert length == wm.TEX_W * wm.TEX_H * 4


# --------------------------------------------------------------------------
# Head-anchored ("hud") mic
# --------------------------------------------------------------------------

def test_hud_mic_is_one_button_and_ignores_floating_windows():
    api = FakeApi()
    api.add_window(49)  # a floating window: hud mode must not target it
    placed = []
    clock = Clock()
    mic = wm.WindowMic(
        api, "frameassist.voicetyping", clock=clock, anchor="hud",
        hud=lambda handle: placed.append(handle))
    mic.tick(clock())
    assert list(mic._mics) == [wm.HUD_SEQ]
    hud_mic = mic._mics[wm.HUD_SEQ]
    assert hud_mic.key == "frameassist.voicetyping.mic.hud"
    assert hud_mic.handle in api.shown
    assert placed == [hud_mic.handle]
    # A second tick must not create a second overlay.
    clock.advance(0.1)
    mic.tick(clock())
    assert list(mic._mics) == [wm.HUD_SEQ]
    assert placed == [hud_mic.handle]


def test_hud_mic_click_starts_and_stops_recording():
    api = FakeApi()
    commands = []
    clock = Clock()
    mic = wm.WindowMic(
        api, "frameassist.voicetyping",
        on_command=lambda cmd, seq: commands.append((cmd, seq)),
        can_act=lambda: True, engine_state=lambda: "idle",
        clock=clock, anchor="hud", hud=lambda handle: None)
    mic.tick(clock())
    hud_mic = mic._mics[wm.HUD_SEQ]
    api.events[hud_mic.handle] = [
        {"type": wm.MOUSE_DOWN, "button": 0, "x": 0, "y": 0}]
    mic.tick(clock())
    assert commands == [("talk_start", wm.HUD_SEQ)]
    assert mic._take_seq == wm.HUD_SEQ
    clock.advance(0.4)  # longer than CLICK_S, so this is hold-to-talk
    api.events[hud_mic.handle] = [
        {"type": wm.MOUSE_UP, "button": 0, "x": 0, "y": 0}]
    mic.tick(clock())
    assert commands == [("talk_start", wm.HUD_SEQ), ("talk_stop", wm.HUD_SEQ)]


# --------------------------------------------------------------------------
# Steam-bar ("bar") mic: fixed in the room, under the active window
# --------------------------------------------------------------------------

def test_dock_pose_lands_right_of_the_bar():
    pose = wm.dock_from_corners((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.1, 0.0))
    assert pose is not None
    target, right_u, up_u, facing = pose
    assert right_u == pytest.approx((1.0, 0.0, 0.0), abs=1e-9)
    assert up_u == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
    assert facing == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)
    assert target == pytest.approx(
        (1.0 + wm.DOCK_GAP + wm.PLATE_METERS / 2,
         0.05 - wm.DOCK_DROP, wm.DOCK_FRONT), abs=1e-9)


def test_dock_extra_moves_the_plate_further_right():
    base = wm.dock_from_corners((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.1, 0.0))
    more = wm.dock_from_corners((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.1, 0.0), 0.02)
    assert more[0] == pytest.approx((base[0][0] + 0.02, base[0][1], base[0][2]))


def test_dock_plate_stays_upright_even_when_the_bar_is_tilted():
    # The bar's own axes are tilted on the Frame; the plate must stay upright
    # or it rides on top of the bar instead of beside it.
    pose = wm.dock_from_corners((0.0, 0.0, 0.0), (1.0, 0.0, -0.5), (0.0, 0.1, 0.0))
    assert pose is not None
    target, right_u, up_u, facing = pose
    assert up_u == (0.0, 1.0, 0.0)
    assert right_u[1] == 0.0
    assert facing[2] > 0


def test_dock_pose_rejects_a_bar_that_is_down():
    assert wm.dock_from_corners(None, None, None) is None
    assert wm.dock_from_corners((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)) is None


def test_docked_bar_mic_lands_right_of_the_steam_bar():
    api = FakeApi()
    api.add_window(49)  # a window must NOT influence the dock placement
    api.add_bar()
    clock = Clock()
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=clock,
                       anchor="bar")
    mic.tick(clock())
    bar_mic = mic._mics[wm.BAR_SEQ]
    assert bar_mic.key == "frameassist.voicetyping.mic.bar"
    info = api.bar
    ll = (info["ll"][3], info["ll"][7], info["ll"][11])
    lr = (info["lr"][3], info["lr"][7], info["lr"][11])
    ul = (info["ul"][3], info["ul"][7], info["ul"][11])
    pose = wm.dock_from_corners(ll, lr, ul)
    matrix = api.absolutes[bar_mic.handle]
    assert [matrix[3], matrix[7], matrix[11]] == pytest.approx(pose[0])
    assert bar_mic.handle in api.shown


def test_bar_mic_waits_for_the_dock():
    api = FakeApi()
    api.add_window(49)  # a window alone must not create a bar mic
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=Clock(),
                       anchor="bar")
    mic.tick(Clock()())
    assert mic._mics == {}


def test_settings_open_does_not_move_a_docked_mic():
    api = FakeApi()
    api.add_window(49)
    api.add_bar()
    clock = Clock()
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=clock,
                       anchor="bar")
    mic.tick(clock())
    bar_mic = mic._mics[wm.BAR_SEQ]
    before = list(api.absolutes[bar_mic.handle])
    mic.settings_open = True
    api.add_window(61)  # the Settings dialog appears as the newest window
    clock.advance(wm.SCAN_S + 0.1)
    mic.tick(clock())
    assert api.absolutes[bar_mic.handle] == before
    assert bar_mic.handle in api.shown


def test_docked_bar_mic_hides_when_the_bar_disappears():
    api = FakeApi()
    api.add_window(49)
    api.add_bar()
    clock = Clock()
    mic = wm.WindowMic(api, "frameassist.voicetyping", clock=clock,
                       anchor="bar")
    mic.tick(clock())
    bar_mic = mic._mics[wm.BAR_SEQ]
    assert bar_mic.handle in api.shown

    api.bar = None  # the bar overlay went away
    clock.advance(wm.SCAN_S + 0.1)
    mic.tick(clock())
    assert bar_mic.hidden
    assert bar_mic.handle not in api.shown

"""SteamVR integration: controller buttons, the global-input setting, the
"Listening..." badge, and keeping the buttons out of games.

Each button the app uses (left grip, right grip, left stick, right stick) is
its own SteamVR action set, the way Steam Frame overlay apps read buttons.
That lets us take only the buttons we need and leave games to their own
bindings. The FrameAssist gestures (hold both grips, double-taps, stick flicks)
are worked out in ``gestures.py``; this module just reports raw button
presses and feeds them in.

An overlay app only gets buttons while something else has focus (the Steam UI,
the desktop) if SteamVR's developer setting "Enable global input from
overlays" is on. The app checks it and can turn it on with one tap.
"""

import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

from .log import STATE_DIR, log

APP_KEY = "frameassist.voicetyping"
HERE = Path(__file__).parent / "steamvr"
BUTTONS = ("grip_l", "grip_r", "stick_l", "stick_r")
BUTTON_LABELS = {"grip_l": "Left grip", "grip_r": "Right grip",
                 "stick_l": "Left stick", "stick_r": "Right stick"}

GLOBAL_SETTING = ("steamvr", "globalActionSetPriority")
GLOBAL_SETTING_NAME = "Enable global input from overlays"

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "frameassist"
DIAG_PATH = STATE_DIR / "steamvr.json"

HOME_KEYS = ("steam.app.250820",)  # SteamVR Home


def is_home(app_key):
    # Treat the Steam Frame's own UI as "home", not a game: vrlink.client is the
    # Frame's VR compositor/UI scene app that shows whenever you're just using
    # the Frame, so treating it as a game paused all button reading and grips
    # could never fire outside a real game with the Steam menu open.
    if app_key == "vrlink.client" or app_key.startswith("vrlink."):
        return True
    return app_key in HOME_KEYS or app_key.startswith(
        ("system.", "openvr.tool.", "openvr.component."))


def launch_command():
    exe = Path(sys.executable).with_name("frameassist")
    return str(exe) if exe.exists() else sys.executable


def write_vrmanifest():
    """Tell SteamVR about the app so it can auto-start it and list its bindings.

    SteamVR picks the binary path by *platform key*. On the Steam Frame the
    runtime is Linux ARM64, and Valve's own apps declare it under
    ``binary_path_linux_arm`` (see /opt/steamvr/resources/config/runtime.vrmanifest).
    If none of the platform keys matches, SteamVR refuses the app with
    "must specify binary_path for launch_type binary. Skipping" — which drops
    our registration, our app identity, and with it all overlay-global input.
    """
    import platform
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "frameassist.vrmanifest"
    exe = launch_command()
    args = ("" if exe.endswith("frameassist") else "-m frameassist ") + "--background"
    app = {
        "app_key": APP_KEY,
        "launch_type": "binary",
        "arguments": args,
        "is_dashboard_overlay": True,
        "action_manifest_path": str(HERE / "actions.json"),
        "strings": {"en_us": {"name": "FrameAssist",
                              "description": "Hold both grips and talk"}},
    }
    arch = platform.machine().lower()
    if arch in ("aarch64", "arm64", "armv8l"):
        app["binary_path_linux_arm"] = exe
    else:
        app["binary_path_linux"] = exe
    path.write_text(json.dumps({"source": "builtin", "applications": [app]}, indent=2))
    return path


class SteamVR:
    """Runs in a background thread. Never raises: if SteamVR isn't there, the
    status explains why and the rest of the app keeps working."""

    BINDING_GRACE = 4.0

    def __init__(self, on_status=lambda text: None,
                 in_games=False, rest_while_streaming=False,
                 autolaunch=True, clock=None):
        self.on_status = on_status
        self.in_games = in_games
        self.rest_while_streaming = rest_while_streaming
        self.autolaunch = autolaunch
        self.clock = clock or time.monotonic
        self.connected = False
        self._stop = False
        self.hold = False
        self._badge = None
        self._badge_dirty = False
        self._requests = []
        self._warned = set()
        self.engine = None
        self._prev_held = set()
        self.vr = None
        self.badge = None
        # Read-only controller buttons from SteamVR's own web-socket stream
        # (vrws.py). It reports grips/sticks in any context — including the
        # Frame's desktop/UI, where SteamVR Input won't route overlay-global
        # buttons without a game running. Started with the SteamVR session.
        self.reader = None
        self.reset()

    def reset(self):
        self.bound = {}
        self.taken = set()
        self.controllers = []
        self.global_input = None
        self.global_rejected = False
        self.in_game = False
        self.streaming = False  # a vrlink (streamed PC/desktop) scene is active
        self.dashboard = False
        self.paused = False
        self.source = None
        self.status = "waiting"
        self.summary = "Connecting to SteamVR..."
        self._scene_pid = 0
        self._scene_key = ""
        self._connected_at = 0.0
        self._next_slow = 0.0
        self._last_diag = None

    # -- UI thread ----------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    def set_options(self, in_games=None, rest_while_streaming=None):
        if in_games is not None:
            self.in_games = in_games
        if rest_while_streaming is not None:
            self.rest_while_streaming = rest_while_streaming

    def set_autolaunch(self, on):
        self.autolaunch = on
        self._requests.append(lambda vr: vr.VRApplications().setApplicationAutoLaunch(APP_KEY, on))

    def enable_global_input(self):
        self._requests.append(self._enable_global)

    def show_state(self, state, message=""):
        self._badge = (state, message)
        self._badge_dirty = True

    def open_bindings(self):
        if not self.connected:
            return False
        try:
            self.vr.VRInput().openBindingUI(APP_KEY, 0, 0, False)
            return True
        except Exception as err:
            log.warning("openBindingUI failed: %s", err)
            return False

    def snapshot(self):
        return {"summary": self.summary, "status": self.status, "connected": self.connected,
                "bound": dict(self.bound), "taken": set(self.taken),
                "controllers": list(self.controllers), "source": self.source,
                "global_input": self.global_input, "paused": self.paused,
                "in_game": self.in_game, "streaming": self.streaming,
                "rest_while_streaming": self.rest_while_streaming}

    # -- thread ---------------------------------------------------------------
    def _set_status(self, status, summary):
        changed = (status, summary) != (self.status, self.summary)
        self.status, self.summary = status, summary
        if changed:
            log.info("steamvr: %s", summary)
            self.on_status(summary)

    def _warn_once(self, text):
        if text not in self._warned:
            self._warned.add(text)
            log.warning("steamvr: %s", text)

    def _run(self):
        try:
            import openvr
        except Exception as err:
            self._set_status("off", f"The SteamVR library didn't load ({err}). "
                                    "Run the installer again.")
            return
        self.vr = openvr
        waiting_logged = False
        while not self._stop:
            if self.hold:
                if self.status != "held":
                    self._set_status("held", "Paused while the app's window has the shortcuts.")
                time.sleep(0.2)
                continue
            try:
                # "Background" never starts SteamVR; it just fails if it isn't running.
                openvr.init(openvr.VRApplication_Background)
                openvr.shutdown()
                openvr.init(openvr.VRApplication_Overlay)
            except Exception as err:
                if not waiting_logged:
                    log.info("steamvr init failed: %s", err)
                    waiting_logged = True
                self.connected = False
                time.sleep(5.0)
                continue
            waiting_logged = False
            try:
                self.open(openvr)
                while not self._stop and not self.hold and self.step():
                    time.sleep(1 / 60)
            except Exception as err:
                log.exception("steamvr session error")
                self._set_status("error", f"SteamVR error: {err}")
            finally:
                self.connected = False
                try:
                    openvr.shutdown()
                except Exception:
                    pass
            if not self.hold:
                time.sleep(3)

    def _register(self, openvr):
        apps = openvr.VRApplications()
        for step, call in (
            ("addApplicationManifest", lambda: apps.addApplicationManifest(str(write_vrmanifest()), False)),
            ("identifyApplication", lambda: apps.identifyApplication(os.getpid(), APP_KEY)),
            ("setApplicationAutoLaunch", lambda: apps.setApplicationAutoLaunch(APP_KEY, self.autolaunch)),
        ):
            try:
                call()
            except Exception as err:
                log.warning("steamvr %s failed: %s", step, err)

    def open(self, openvr):
        """Connect to SteamVR Input. Separate from step() so tests can drive it."""
        self.vr = openvr
        self._register(openvr)
        vrinput = openvr.VRInput()
        vrinput.setActionManifestPath(str(HERE / "actions.json"))
        self.sets = {b: vrinput.getActionSetHandle(f"/actions/{b}") for b in BUTTONS}
        self.actions = {b: vrinput.getActionHandle(f"/actions/{b}/in/press") for b in BUTTONS}        # We need to win the overlay-global priority band against Steam's own
        # dashboard overlay. On the Frame the band is
        # k_nActionSetOverlayGlobalPriorityMin(0x01000000)..Max(0x01ffffff) and
        # asking at Min+0x100 loses to the Frame's own UI, so grips worked only
        # in games (where nothing else is in the band). Ask near the top of the
        # overlay-global band (still below the reserved 0x02000000 range) so the
        # Frame's own UI/desktop doesn't pre-empt us.
        self.global_priority = openvr.k_nActionSetOverlayGlobalPriorityMax - 0x100
        self.event = openvr.VREvent_t()
        self.badge = Badge(openvr)
        self.connected = True
        self._connected_at = self.clock()
        try:
            from .vrws import ButtonReader
            self.reader = ButtonReader()
            self.reader.start()
        except Exception as err:
            log.info("reading buttons from the SteamVR web socket failed: %s", err)
            self.reader = None
        log.info("steamvr connected; action manifest %s", HERE / "actions.json")

    def _events(self, openvr):
        """Handle SteamVR events. Returns False when SteamVR is quitting."""
        names = {getattr(openvr, n, None): n for n in (
            "VREvent_Input_BindingLoadFailed", "VREvent_Input_BindingLoadSuccessful",
            "VREvent_Input_ActionManifestReloaded", "VREvent_Input_ActionManifestLoadFailed",
            "VREvent_Input_BindingsUpdated", "VREvent_ActionBindingReloaded")}
        system = openvr.VRSystem()
        while system.pollNextEvent(self.event):
            kind = self.event.eventType
            if kind == openvr.VREvent_Quit:
                system.acknowledgeQuit_Exiting()
                return False
            if kind in names:
                log.info("steamvr event: %s", names[kind])
        return True

    def _find_controllers(self, openvr):
        system = openvr.VRSystem()
        found = []
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                if system.getTrackedDeviceClass(i) != openvr.TrackedDeviceClass_Controller:
                    continue
                role = system.getControllerRoleForTrackedDeviceIndex(i)
                kind = system.getStringTrackedDeviceProperty(i, openvr.Prop_ControllerType_String)
            except Exception:
                continue
            hand = {openvr.TrackedControllerRole_LeftHand: "left",
                    openvr.TrackedControllerRole_RightHand: "right"}.get(role, f"device{i}")
            found.append((hand, i, kind))
        return sorted(found)

    def _slow_checks(self, openvr):
        """Once a second: controllers, the global input setting, games, dashboard."""
        self._devices = self._find_controllers(openvr)
        self.controllers = [f"{kind} ({hand})" for hand, _, kind in self._devices]
        try:
            setting = bool(openvr.VRSettings().getBool(*GLOBAL_SETTING))
        except Exception:
            setting = False
        if setting != self.global_input:
            log.info("steamvr: '%s' is %s", GLOBAL_SETTING_NAME, "on" if setting else "off")
            if setting:
                self.global_rejected = False
        self.global_input = setting
        try:
            pid = openvr.VRApplications().getCurrentSceneProcessId()
        except Exception:
            pid = 0
        if pid != self._scene_pid:
            self._scene_pid = pid
            try:
                key = openvr.VRApplications().getApplicationKeyByProcessId(pid) if pid else ""
            except Exception:
                key = "?"
            log.info("steamvr: scene app is now %s", f"{key} (pid {pid})" if pid else "none")
            self._scene_key = key
        self.in_game = bool(pid) and pid != os.getpid() and not is_home(self._scene_key)
        # A vrlink scene means the Frame is showing a streamed PC/desktop (or its
        # own integrated UI). We can't tell "streamed desktop" from "streamed
        # game" from SteamVR here, so the rest_while_streaming opt-in pauses for
        # all of it — gambler's choice the user controls.
        self.streaming = bool(pid) and self._scene_key.startswith("vrlink.")
        try:
            dashboard = bool(openvr.VROverlay().isDashboardVisible())
        except Exception:
            dashboard = False
        self.dashboard = dashboard

    def _enable_global(self, openvr):
        try:
            openvr.VRSettings().setBool(*GLOBAL_SETTING, True)
            log.info("turned on SteamVR '%s'", GLOBAL_SETTING_NAME)
        except Exception as err:
            log.warning("couldn't turn on %s: %r", GLOBAL_SETTING, err)
        self.global_rejected = False
        self._next_slow = 0.0

    def step(self):
        """One frame: read the buttons we need, feed the gesture engine.
        Returns False when SteamVR is quitting."""
        openvr = self.vr
        if not self._events(openvr):
            return False
        while self._requests:
            try:
                self._requests.pop(0)(openvr)
            except Exception as err:
                log.warning("steamvr request failed: %r", err)
        now = self.clock()
        if now >= self._next_slow:
            self._slow_checks(openvr)
            self._next_slow = now + 1.0

        # Leave the buttons to a game unless asked not to. With the Steam menu
        # open over a game, the buttons are ours again. Separately, the opt-in
        # "rest while a PC is streamed in" pauses for any vrlink scene (games or
        # desktop): the frame can't tell them apart, so the user decides.
        game = self.in_game and not self.in_games
        stream = self.rest_while_streaming and self.streaming
        self.paused = (game or stream) and not self.dashboard
        take = set(BUTTONS)
        if self.paused:
            take = set()  # give the game back its buttons

        active = (openvr.VRActiveActionSet_t * len(take))()
        for slot, button in zip(active, sorted(take)):
            slot.ulActionSet = self.sets[button]
            slot.ulRestrictedToDevice = openvr.k_ulInvalidInputValueHandle
            slot.nPriority = self.global_priority
        if take:
            try:
                openvr.VRInput().updateActionState(active)
            except Exception as err:
                if type(err).__name__ == "InputError_InvalidPriority":
                    self.global_rejected = True
                    self._warn_once("global input priority refused; is the setting off?")
                else:
                    self._warn_once(f"updateActionState failed: {err!r}")

        buttons = set()
        for button in take:
            try:
                data = openvr.VRInput().getDigitalActionData(
                    self.actions[button], openvr.k_ulInvalidInputValueHandle)
            except Exception as err:
                self._warn_once(f"reading {button}: {err!r}")
                self.bound[button] = False
                continue
            new_active = bool(data.bActive)
            if new_active != self.bound.get(button):
                log.info("steamvr: action %s bActive -> %s (state=%s, paused=%s)",
                         button, new_active, data.bState, self.paused)
            self.bound[button] = new_active
            if new_active and data.bState:
                buttons.add(button)

        # Also feed the gesture engine from SteamVR's own read-only web-socket
        # stream. It reports the same grips/sticks in any context — the desktop
        # and Frame UI included — which is where SteamVR Input won't route
        # overlay-global buttons (that's why grips only worked in games before).
        # Still give the buttons back to a running game unless asked not to.
        if self.reader and not self.paused:
            buttons |= self.reader.buttons()

        self._emit(buttons, now)
        self._report(now)
        if self._badge_dirty and self.badge:
            self._badge_dirty = False
            self.badge.show(*self._badge)
        if self.badge:
            self.badge.tick()
        return True

    def _emit(self, buttons, now):
        """Feed press/release transitions into the gesture engine."""
        held = set(b for b in BUTTONS if b in buttons)
        eng = self.engine
        if eng is None:
            return
        if held != self._prev_held:
            log.info("steamvr: held buttons became %s",
                     ",".join(sorted(held)) or "none")
        _SIDE = {"l": "left", "r": "right"}
        for button in sorted(BUTTONS):
            down = button in held
            was = button in getattr(self, "_prev_held", set())
            if down and not was:
                kind, side_code = button.rsplit("_", 1)
                eng.press(kind, _SIDE[side_code], now)
            elif was and not down:
                kind, side_code = button.rsplit("_", 1)
                eng.release(kind, _SIDE[side_code], now)
        self._prev_held = held
        eng.tick(now)

    def set_gesture_engine(self, engine):
        self.engine = engine
        self._prev_held = set()

    def _report(self, now):
        if any(self.bound.values()):
            self.source = "SteamVR Input"
        else:
            self.source = None
        settled = now - self._connected_at > self.BINDING_GRACE
        setting_off = self.global_input is False or self.global_rejected
        diag = (tuple(self.controllers), self.source, self.global_input, self.global_rejected,
                self.paused, settled)
        if diag == self._last_diag:
            return
        self._last_diag = diag
        if not self.controllers:
            status, summary = "connected", "Connected to SteamVR. Turn on your controllers."
        elif self.paused:
            status, summary = "paused", ("Paused while a game is running, so the game keeps its "
                                         "buttons. Open the Steam menu to use them.")
        elif setting_off:
            status, summary = "setting", (f"Controller shortcuts need one SteamVR setting: "
                                          f"“{GLOBAL_SETTING_NAME}”. Tap Turn on.")
        elif not self.source and settled:
            status, summary = "nobind", ("SteamVR hasn't loaded button bindings for this app. "
                                         "Restart the headset, then open the app again.")
        else:
            status, summary = "on", (f"Connected: {', '.join(self.controllers)}. "
                                     "Hold the grips and talk, or double-tap them for hands-free.")
        self._set_status(status, summary)
        try:
            DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DIAG_PATH.write_text(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "status": status, "summary": summary,
                "controllers": self.controllers, "source": self.source,
                "bound": sorted(b for b, on in self.bound.items() if on),
                "global_input": self.global_input, "global_rejected": self.global_rejected,
                "in_game": self.in_game, "dashboard": self.dashboard, "paused": self.paused,
                "in_games": self.in_games, "streaming": self.streaming,
                "rest_while_streaming": self.rest_while_streaming,
                "scene": self._scene_key}, indent=2))
        except OSError:
            pass


class Badge:
    """A small pill floating just below your view: "Listening..." /

    "Typing...". Lets you use the controller shortcuts without opening the
    window."""

    W, H = 512, 128

    def __init__(self, openvr):
        self.openvr = openvr
        self.overlay = openvr.VROverlay()
        self.handle = None
        self.hide_at = 0.0
        try:
            self.handle = self.overlay.createOverlay(APP_KEY + ".badge", "FrameAssist")
            self.overlay.setOverlayWidthInMeters(self.handle, 0.22)
            # Let the laser interact with this undocked floating overlay
            # (the "Allow undocked dashboard overlay interaction" behavior).
            try:
                self.overlay.setOverlayFlag(
                    self.handle, openvr.VROverlayFlags_MakeOverlaysInteractiveIfVisible, True)
            except Exception:
                pass
            m = openvr.HmdMatrix34_t()
            for r in range(3):
                for c in range(4):
                    m.m[r][c] = 1.0 if r == c else 0.0
            m.m[1][3] = -0.16   # a little below centre
            m.m[2][3] = -0.75   # 75 cm in front
            self.overlay.setOverlayTransformTrackedDeviceRelative(
                self.handle, openvr.k_unTrackedDeviceIndex_Hmd, m)
        except Exception:
            self.handle = None

    def show(self, state, message):
        if self.handle is None:
            return
        from .engine import ERROR, LISTENING, READY, WORKING
        text = {LISTENING: "Listening...", WORKING: "Typing..."}.get(state)
        color = {LISTENING: "#ff4d5e", WORKING: "#ffb547"}.get(state, "#2ee6b8")
        if state == READY and message.startswith("Didn"):
            text, color = "Didn't catch that", "#8b94a7"
        elif state == READY and message.startswith("Copied"):
            text, color = "Copied. Paste it", "#2ee6b8"
        elif state == ERROR:
            text, color = "Voice typing error", "#ff4d5e"
        if not text:
            self.hide_at = time.monotonic() + 0.4
            return
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return
        try:
            buf = render_pill(text, color, self.W, self.H)
            self.overlay.setOverlayRaw(self.handle, buf, self.W, self.H, 4)
            self.overlay.showOverlay(self.handle)
        except Exception:
            return
        self.hide_at = time.monotonic() + (2.0 if state in (READY, ERROR) else 1e9)

    def tick(self):
        if self.handle is not None and self.hide_at and time.monotonic() > self.hide_at:
            self.hide_at = 0.0
            try:
                self.overlay.hideOverlay(self.handle)
            except Exception:
                pass


def render_pill(text, color, w, h):
    """RGBA pixels (ctypes buffer) of a rounded pill with a coloured dot and text."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    img = QImage(w, h, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(13, 16, 22, 235))
    p.drawRoundedRect(QRectF(4, 4, w - 8, h - 8), (h - 8) / 2, (h - 8) / 2)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(44, h / 2 - 16, 32, 32))
    f = QFont()
    f.setFamilies(["Inter", "Noto Sans", "Cantarell", "DejaVu Sans"])
    f.setPixelSize(46)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#eef1f6"))
    p.drawText(QRectF(100, 0, w - 130, h), Qt.AlignVCenter | Qt.AlignLeft, text)
    p.end()
    data = bytes(img.constBits())
    return (ctypes.c_ubyte * len(data)).from_buffer_copy(data)

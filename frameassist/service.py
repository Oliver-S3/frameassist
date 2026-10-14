"""The background copy, and how it shares the work with the window.

SteamVR starts the app in the background (see autostart.py), where it runs the
controller shortcuts without a window. Opening the app starts a second copy,
with the window. That copy does the work while it runs and the background copy
steps aside, so nothing is typed twice. The window's copy keeps a connection
to the background copy open, and the background copy takes the work back the
moment that connection ends, however the window's copy ended.

The connection also carries the last dictation, so the window can always copy
it again.
"""

import ctypes
import fcntl
import json
import os
import signal
import socket
import struct
import sys
import threading
import time
import zlib

from . import config as config_mod
from .log import log

GRACE = 3.0


def state_dir():
    from . import log as log_mod
    return log_mod.STATE_DIR


def socket_name(role="background"):
    """An abstract socket (no file, no path length limit) per role, user and
    state folder, like the locks. Abstract sockets have no permissions, so
    both ends check that the other is the same user."""
    folder = zlib.crc32(str(state_dir()).encode())
    return f"\0frameassist-{role}-{os.getuid()}-{folder:08x}"


def place():
    """Where this copy shows windows: the Steam session or the Frame's desktop
    (each has its own display and runtime folder)."""
    return "|".join(os.environ.get(k, "") for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"))


# -- one copy per role ------------------------------------------------------------
_held = {}


def hold_lock(role):
    """Take the lock for this copy's role ("window" or "background"), held
    until the process ends. False if another copy has it."""
    if role in _held:
        return True
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / f"{role}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _held[role] = fd
    return True


def wait_for_lock(role, seconds):
    end = time.monotonic() + seconds
    while not hold_lock(role):
        if time.monotonic() > end:
            return False
        time.sleep(0.1)
    return True


def release_lock(role):
    fd = _held.pop(role, None)
    if fd is not None:
        os.close(fd)


# -- messages: one JSON object per line -------------------------------------------
def send(sock, message):
    sock.sendall(json.dumps(message).encode() + b"\n")


def receive(stream):
    line = stream.readline(1 << 20)
    if not line:
        raise OSError("connection closed")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("not a message")
    return message


def same_user(sock):
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return False
    return struct.unpack("3i", creds)[1] == os.getuid()


class _Connector:
    def __init__(self, on_window, on_quit, settle=lambda: None):
        self.on_window, self.on_quit = on_window, on_quit
        self.settle = settle
        self.last_text = ""
        self.windows = 0
        self._lock = threading.Lock()
        self._conns = set()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(socket_name())
        self.sock.listen(4)

    def start(self):
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        for conn in list(self._conns):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def has_window(self):
        return self.windows > 0

    def _accept(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        if not same_user(conn):
            conn.close()
            return
        threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        window = quit_all = False
        self._conns.add(conn)
        try:
            conn.settimeout(5)
            stream = conn.makefile("rb")
            try:
                hello = receive(stream).get("hello")
                if hello == "status":
                    send(conn, {"pid": os.getpid(), "windows": self.windows})
                    return
                if hello != "window":
                    return
            except (OSError, ValueError):
                return
            with self._lock:
                self.windows += 1
                window = True
                if self.windows == 1:
                    self.on_window(True)
            self.settle()
            send(conn, {"last_text": self.last_text})
            conn.settimeout(None)
            while True:
                message = receive(stream)
                if isinstance(message.get("last_text"), str):
                    self.last_text = message["last_text"]
                if message.get("quit"):
                    quit_all = True
        except (OSError, ValueError):
            pass  # the usual way a window's copy goes: the connection ends
        finally:
            self._conns.discard(conn)
            conn.close()
            if window:
                with self._lock:
                    self.windows -= 1
                    if quit_all:
                        self.on_quit()
                    elif self.windows == 0:
                        self.on_window(False)


def _connect(timeout, role="background"):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_name(role))
    except OSError:
        sock.close()
        return None
    if not same_user(sock):
        sock.close()
        return None
    return sock


class Link:
    """The window copy's connection to the background copy."""

    def __init__(self, sock, last_text):
        self.sock = sock
        self.last_text = last_text
        self.alive = True
        self._lock = threading.Lock()

    @classmethod
    def open(cls, timeout=5.0):
        sock = _connect(timeout)
        if sock is None:
            return None
        try:
            send(sock, {"hello": "window"})
            reply = receive(sock.makefile("rb"))
        except (OSError, ValueError):
            sock.close()
            return None
        sock.settimeout(None)
        last = reply.get("last_text")
        return cls(sock, last if isinstance(last, str) else "")

    def watch(self, on_lost):
        def run():
            try:
                while self.sock.recv(4096):
                    pass
            except OSError:
                pass
            self.alive = False
            on_lost()
        threading.Thread(target=run, daemon=True).start()

    def send_last(self, text):
        self._send({"last_text": text})

    def quit(self):
        self._send({"quit": True})

    def _send(self, message):
        try:
            with self._lock:
                send(self.sock, message)
        except OSError:
            self.alive = False

    def close(self):
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def status(timeout=2.0):
    """What the background copy says about itself, or None if none is running."""
    sock = _connect(timeout)
    if sock is None:
        return None
    try:
        send(sock, {"hello": "status"})
        return receive(sock.makefile("rb"))
    except (OSError, ValueError):
        return None
    finally:
        sock.close()


class Background:
    """The engine and the controller shortcuts, without a window, stepping
    aside while the window's copy runs."""

    def __init__(self, cfg, engine_cls, vr_cls=None, on_quit=lambda: None,
                 grace=GRACE, copy_clipboard=None):
        if vr_cls is None:
            from .steamvr import SteamVR as vr_cls
        self.cfg = cfg
        self.on_quit = on_quit
        self.grace = grace
        self.ready = False
        self._handed_over = False
        self.copy_clipboard = copy_clipboard or (lambda text: None)
        self.engine = engine_cls(cfg, on_state=self._on_state,
                                 copy_clipboard=self.copy_clipboard)
        self.vr = vr_cls(in_games=cfg.get("in_games", False),
                         autolaunch=cfg.get("autostart", True))
        self.vr.set_gesture_engine(self.engine_gestures())
        self.vr.hold = True
        self.engine.paused = True
        self.server = _Connector(self._window_changed, self._quit, self._settle)

    def engine_gestures(self):
        from .gestures import GestureEngine
        return GestureEngine(lambda cmd: self.engine.hotkey({"cmd": cmd}))

    def start(self):
        self.server.start()
        self.vr.start()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        started = time.monotonic()
        self.engine.load()
        while not self._handed_over and time.monotonic() < started + self.grace:
            time.sleep(0.05)
        self.ready = True
        with self.server._lock:
            self._apply()

    def _window_changed(self, open_now):
        if open_now:
            log.info("the window is open; it has the shortcuts until it closes")
        else:
            log.info("the window closed; the shortcuts run in the background again")
            self._handed_over = True
            self._reload_settings()
        self._apply()

    def _apply(self):
        hold = not self.ready or self.server.has_window()
        self.engine.paused = hold
        self.vr.hold = hold
        if hold:
            self.engine.cancel_talking()

    def _settle(self, timeout=2.0):
        end = time.monotonic() + timeout
        while self.vr.status != "held" and time.monotonic() < end:
            time.sleep(0.02)

    def _reload_settings(self):
        new = config_mod.load()
        self.cfg.update(new)

    def _on_state(self, state, message):
        self.vr.show_state(state, message)

    def _quit(self):
        log.info("quit from the window")
        self.on_quit()


def run_background(cfg, engine_cls):
    """The background copy's main. Exits quietly if one is already running."""
    if not hold_lock("background"):
        log.info("already running in the background")
        return 0
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setApplicationName("FrameAssist")
        app.setQuitOnLastWindowClosed(False)
    except Exception:
        # Qt isn't needed in the background beyond the clipboard; fall back
        # to a headless loop so the shortcuts still work.
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])
        app.setApplicationName("FrameAssist")

    state = {}

    def on_state(s, msg):
        state["engine"] = s
        state["message"] = msg

    def on_copy(text):
        try:
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.clipboard():
                QGuiApplication.clipboard().setText(text)
        except Exception:
            pass

    work = Background(cfg, engine_cls, on_quit=lambda: app.exit(0),
                      copy_clipboard=on_copy)
    work.engine.on_state = on_state
    work.start()
    return app.exec()

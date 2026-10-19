"""Read-only controller buttons from SteamVR's own controller binding page.

vrserver serves its binding page on 127.0.0.1:27062 and streams the
controllers' raw input to it over a web socket. Reading that stream takes
nothing from anyone (the button still does whatever it does in the Steam UI
or a game) and works whatever app has focus — which is exactly the authorative
case SteamVR Input won't serve: an overlay app reading grips while the Frame's
own UI/desktop is in front, not just in a game.

Adapted from frame-voice's vrws.py (MIT, Copyright (c) 2025 the frame-voice
authors), itself adapted from Frametop's input/vrws.py (MIT, Copyright (c)
2026 DeeJanuz); see THIRD_PARTY_NOTICES.md. Standard library only.

Unlike frame-voice (which follows only the right controller's A/B/X/Y/
trigger), FrameAssist follows both controllers' grips and thumbstick clicks,
because our gestures use both hands.
"""

import base64
import json
import os
import select
import socket
import struct
import threading
import time
import urllib.request

from .log import log

HOST, PORT = "127.0.0.1", 27062
ORIGIN = f"http://{HOST}:{PORT}"
# vrserver answers its binding page's own requests; these headers make ours
# look like them.
HEADERS = {"Referer": f"{ORIGIN}/dashboard/controllerbinding.html"}

# Component paths -> our button name stem; the suffix is _l / _r from the side.
COMPONENTS = {
    "/input/grip/click": "grip",
    "/input/thumbstick/click": "stick",
    "/input/joystick/click": "stick",
}

SIDE_SUFFIX = {"left": "l", "right": "r"}

BUTTONS = ("grip_l", "grip_r", "stick_l", "stick_r")


def controllers():
    """Root paths + side of the Frame controllers SteamVR has now."""
    req = urllib.request.Request(f"{ORIGIN}/input/getstate.json", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=3) as resp:
        state = json.load(resp)
    out = []
    for d in state.get("devices", []):
        if d.get("controller_type") == "frame_controller" and d.get("root_path"):
            side = d.get("side")
            if side in ("left", "right"):
                out.append((d["root_path"], side))
    return sorted(out)


class VrSocket:
    """A minimal RFC 6455 web socket client for vrserver."""

    def __init__(self, timeout=3, sock=None):
        self.buf = bytearray()
        self.parts = bytearray()  # a fragmented message so far
        self.mailbox = ""
        if sock is not None:  # tests
            self.sock = sock
            return
        self.sock = socket.create_connection((HOST, PORT), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nUpgrade: websocket\r\n"
                               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                               f"Sec-WebSocket-Version: 13\r\nOrigin: {ORIGIN}\r\n"
                               f"Referer: {HEADERS['Referer']}\r\n\r\n").encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise OSError("vrserver closed the connection during the handshake")
                head += chunk
            head, rest = head.split(b"\r\n\r\n", 1)
            status = head.split(b"\r\n", 1)[0]
            if b" 101 " not in status + b" ":
                raise OSError(f"vrserver refused the web socket: {status.decode(errors='replace')}")
        except BaseException:
            self.sock.close()
            raise
        self.sock.settimeout(None)
        self.buf += rest

    def close(self):
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()

    def _frame(self, opcode, payload):
        """One frame: final, masked (as a client's must be)."""
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text):
        self._frame(0x1, text.encode())

    def open(self, mailbox):
        self.send(f"mailbox_open {mailbox}")
        self.mailbox = mailbox

    def subscribe(self, path):
        self.send("mailbox_send input_server " + json.dumps(
            {"type": "request_input_state_updates", "device_path": path,
             "returnAddress": self.mailbox}))

    def messages(self, timeout=None):
        """Text messages that came in; waits up to `timeout` seconds for some.
        Raises OSError when the connection ends."""
        out = self._parse()
        if out:
            return out
        if not select.select([self.sock], [], [], timeout)[0]:
            return []
        chunk = self.sock.recv(65536)
        if not chunk:
            raise OSError("vrserver closed the web socket")
        self.buf += chunk
        return self._parse()

    def _parse(self):
        """Every whole frame in the buffer; a partial one waits for the rest."""
        out, buf, pos = [], self.buf, 0
        while len(buf) - pos >= 2:
            b0, b1 = buf[pos], buf[pos + 1]
            n, head = b1 & 0x7F, 2
            if n == 126:
                if len(buf) - pos < 4:
                    break
                n, head = struct.unpack_from(">H", buf, pos + 2)[0], 4
            elif n == 127:
                if len(buf) - pos < 10:
                    break
                n, head = struct.unpack_from(">Q", buf, pos + 2)[0], 10
            masked = b1 & 0x80
            if masked:
                head += 4
            if len(buf) - pos < head + n:
                break
            data = bytes(buf[pos + head:pos + head + n])
            if masked:
                mask = buf[pos + head - 4:pos + head]
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            pos += head + n
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise OSError("vrserver closed the web socket")
            if opcode == 0x9:
                self._frame(0xA, data)  # ping: pong
            elif opcode in (0x0, 0x1, 0x2):
                self.parts += data
                if b0 & 0x80:
                    out.append(self.parts.decode(errors="replace"))
                    self.parts = bytearray()
        del buf[:pos]
        return out


def apply_message(text, devices, state):
    """Update state ({button: down}) from one message about the given devices.
    ``devices`` is a dict of root_path -> side; ``state`` maps button->bool."""
    if "update_component_states" not in text:
        return
    try:
        msg = json.loads(text)
    except ValueError:
        return
    side = devices.get(msg.get("device"))
    if side is None:
        return
    suffix = SIDE_SUFFIX.get(side)
    if suffix is None:
        return
    for comp, value in (msg.get("components") or {}).items():
        name = COMPONENTS.get(comp)
        if name:
            state[f"{name}_{suffix}"] = bool(value)


class ButtonReader:
    """Follows both Frame controllers' grips and sticks in a background thread."""

    def __init__(self):
        self.connected = False
        self._pressed = frozenset()
        self._stop = False
        self._thread = None
        self._last_error = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop = True

    def buttons(self):
        return set(self._pressed) if self.connected else set()

    def _run(self):
        while not self._stop:
            try:
                self._session()
            except Exception as err:  # undocumented protocol: anything can go wrong
                text = f"{type(err).__name__}: {err}"
                if text != self._last_error:
                    self._last_error = text
                    log.info("steamvr web socket: %s", text)
            finally:
                # never leave a button looking held
                self.connected = False
                self._pressed = frozenset()
            time.sleep(5)

    def _session(self):
        devices = controllers()
        if not devices:
            raise OSError("no Frame controller on the web socket")
        ws = VrSocket()
        try:
            ws.open(f"frameassist_{os.getpid()}")
            for path, _side in devices:
                ws.subscribe(path)
            self.connected = True
            self._last_error = None
            paths = {path: side for path, side in devices}
            log.info("steamvr web socket: following %s",
                     ", ".join(f"{side} {path}" for path, side in devices))
            state, next_check = {}, time.monotonic() + 10
            while not self._stop:
                for text in ws.messages(timeout=0.5):
                    apply_message(text, paths, state)
                self._pressed = frozenset(b for b, down in state.items() if down)
                if time.monotonic() > next_check:  # a controller may come back under a new path
                    next_check = time.monotonic() + 10
                    if [p for p, _ in controllers()] != [p for p, _ in devices]:
                        return
        finally:
            ws.close()

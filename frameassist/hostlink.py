"""Talking to the PC helper over the LAN for "Type into a streamed PC".

FrameAssist types into the Frame itself with a ``uinput`` keyboard
(:mod:`frameassist.keyboard`). Those keystrokes land on the Frame's own
compositor, so they never reach a Windows desktop that is only being *streamed*
into VR as video. The only way to type there is to send the finished text to
the host PC itself, where a small companion injects it with Win32 ``SendInput``.

:class:`HostTypist` is that headset-side client. It exposes the **same method
surface as** :class:`frameassist.keyboard.Typist` (``type_text`` / ``shortcut``
/ ``close``) so it drops into the engine's ``_need_typist()`` seam unchanged,
plus ``copy_text`` (the copy gesture bypasses the typist) and ``ping`` (the
"Test connection" button).

Wire format: newline-delimited JSON, one object per line, using the exact codec
in :mod:`frameassist.service` (``send``/``receive``). Every request carries an
HMAC-SHA256 over its serialised body keyed by the pairing code, plus a
monotonic per-connection ``seq`` so a random LAN device cannot type into the
PC and a captured packet cannot be replayed. The code is never sent in the
clear. Timeouts are deliberately tight (2-3 s): the caller holds the engine
lock while delivering, so a dead or hung helper must fail fast, not stall
gesture handling.
"""

import hashlib
import hmac
import json
import socket
import threading

# The headset talks the same wire format as the two-copy link, so reuse the
# exact codec rather than a copy of it. service.py is Linux-only (it imports
# fcntl), so on other platforms -- notably the headless test box -- fall back to
# a byte-identical copy of those two functions; the on-device path uses the real
# ones. The Windows companion re-implements the same two functions (it can't
# import this package).
try:
    from .service import receive, send
except ImportError:  # pragma: no cover - exercised on non-Linux dev/test boxes
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


# The gesture names the engine passes to shortcut() (via the shared Typist
# seam) mapped to the helper's op names. These exact names must exist for
# _need_typist() seam compatibility.
OP_FOR = {
    "paste": "paste",
    "enter": "enter",
    "escape": "escape",
    "backspace": "backspace",
}

PROTOCOL_VERSION = 1


class HostUnavailable(OSError):
    """The PC helper can't be reached or rejected the request.

    A subclass of ``OSError`` so callers can treat it like the socket failures
    they already handle, and so a failed delivery is distinguishable from a
    programming error (an unknown shortcut name is still a ``KeyError``).
    """


def _canonical(body):
    """The exact bytes the HMAC is computed over: stable key order and
    separators, so the companion (a separate implementation) recomputes the
    same digest from the same body."""
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret, body):
    """HMAC-SHA256 of ``body``'s canonical serialisation, keyed by ``secret``."""
    return hmac.new(secret.encode("utf-8"), _canonical(body), hashlib.sha256).hexdigest()


def verify(secret, message):
    """True if ``message``'s ``hmac`` field matches its body, recomputed in
    constant time. The ``hmac`` field itself is excluded from the signed body."""
    got = message.get("hmac")
    if not isinstance(got, str):
        return False
    body = {k: v for k, v in message.items() if k != "hmac"}
    return hmac.compare_digest(got, sign(secret, body))


class HostTypist:
    """A persistent, lazily-connected client for one PC helper.

    ``addr``/``port``/``secret`` come straight from config. The connection is
    opened on first use and reused; any transport failure or server rejection
    closes it so the next call reconnects fresh, and surfaces as
    :class:`HostUnavailable`.
    """

    def __init__(self, addr, port, secret, timeout=2.5):
        self.addr = addr
        self.port = int(port)
        self.secret = secret
        # Tight on purpose: a hung helper must not stall the gesture thread.
        self.timeout = timeout
        self._sock = None
        self._stream = None
        self._seq = 0
        self._lock = threading.Lock()

    # -- connection ---------------------------------------------------------
    def _connect(self):
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.addr, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
            self._stream = self._sock.makefile("rb")
            self._seq = 0  # seq is monotonic per connection, reset on (re)connect
        except OSError as exc:
            self._drop()
            raise HostUnavailable(f"can't reach the PC helper at {self.addr}:{self.port}") from exc

    def _drop(self):
        stream, self._stream = self._stream, None
        sock, self._sock = self._sock, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # -- request / reply ----------------------------------------------------
    def _request(self, op, text=None):
        with self._lock:
            self._connect()
            body = {"v": PROTOCOL_VERSION, "op": op, "seq": self._seq}
            if text is not None:
                body["text"] = text
            self._seq += 1
            message = dict(body)
            message["hmac"] = sign(self.secret, body)
            try:
                send(self._sock, message)
                reply = receive(self._stream)
            except OSError as exc:
                self._drop()
                raise HostUnavailable(f"lost the PC helper at {self.addr}:{self.port}") from exc
            except ValueError as exc:
                self._drop()
                raise HostUnavailable("the PC helper sent a malformed reply") from exc
            if not reply.get("ok"):
                self._drop()
                raise HostUnavailable(str(reply.get("error") or "the PC helper rejected the request"))
            return reply

    # -- Typist surface -----------------------------------------------------
    def type_text(self, text):
        """Send finished dictation verbatim (full Unicode: no keymap dropping)."""
        self._request("type", text)

    def shortcut(self, name):
        """Map a gesture name to the helper's op and send it.

        Unknown names raise ``KeyError``, mirroring keyboard.Typist.shortcut.
        """
        self._request(OP_FOR[name])

    def close(self):
        self._drop()

    # -- beyond the Typist surface -----------------------------------------
    def copy_text(self, text):
        """Set the *host's* clipboard (the copy gesture bypasses the typist)."""
        self._request("copy", text)

    def ping(self):
        """True if the helper is reachable and accepts our pairing code."""
        try:
            self._request("ping")
            return True
        except HostUnavailable:
            return False

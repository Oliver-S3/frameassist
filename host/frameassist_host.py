"""FrameAssist PC helper — the Windows end of the streamed-PC dictation link.

This is the companion the FrameAssist headset app talks to when its delivery
mode is set to "A streamed PC". You cannot inject keystrokes into a video
stream, so the headset sends the *finished* dictation here over the LAN and
this program types it into whatever window already has focus on this PC, using
Win32 ``SendInput``.

It is deliberately a single, self-contained, stdlib-only file so it can be
frozen into one double-clickable ``FrameAssistHost.exe`` with no Python or pip
on the target machine.

WIRE PROTOCOL (must stay byte-exact with ``frameassist/hostlink.py``)
---------------------------------------------------------------------
Newline-delimited JSON, one object per line (readline capped at ``1 << 20``),
the same framing the headset uses. Every request looks like::

    {"v": 1, "op": "type|enter|escape|paste|copy|backspace|ping",
     "text": "<unicode>", "seq": <int>, "hmac": "<hex>"}

The ``hmac`` is HMAC-SHA256 (hex digest) over the *canonical* serialisation of
the body — the message with the ``hmac`` field removed — keyed by the pairing
code. Canonical serialisation is exactly::

    json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

with the default ``ensure_ascii=True`` (see ``_canonical`` below and finding F2
in ``docs/phase1-review-findings.md``). An independent implementation that
serialises any differently — different separators, key order, or ``ensure_ascii``
— computes a different digest and is rejected. The pairing code itself is never
put on the wire and never returned.

A per-connection ``seq`` must strictly increase; a lower-or-repeated value is
rejected, which blunts replay of a captured packet.

FOCUS / CORRECTNESS RULE (read this)
------------------------------------
``SendInput`` delivers to the PC's *existing foreground window* regardless of
who calls it. That is exactly what we want (keys land in Notepad, the browser,
…), but it means this program MUST NEVER be the foreground window itself, or it
would type into its own console and eat the keystrokes. So: keep this window
minimised or in the tray, and never call ``SetForegroundWindow``. The console
prints a reminder to the same effect at startup.
"""

import hashlib
import hmac
import json
import os
import socket
import sys
import threading

DEFAULT_PORT = 8763

# Ops this companion understands. The headset sends exactly these.
OPS = frozenset({"type", "enter", "escape", "paste", "copy", "backspace", "ping"})

# Ops that carry text that must be present (and must be a string).
_TEXT_OPS = frozenset({"type", "copy"})

# Pairing-code alphabet: 32 unambiguous characters (no I/O/0/1). Eight of them
# carry exactly 40 bits, matching the "~40-bit human-typeable" pairing code.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# -- canonicalisation / HMAC (byte-exact with frameassist/hostlink.py) --------
def _canonical(body):
    """The exact bytes the HMAC is computed over.

    Stable key order and compact separators, with the default ``ensure_ascii``.
    This MUST match ``frameassist/hostlink.py::_canonical`` byte-for-byte or the
    two ends never agree on a digest (finding F2).
    """
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret, body):
    """HMAC-SHA256 hex digest of ``body``'s canonical serialisation, keyed by
    ``secret``. Mirrors ``frameassist/hostlink.py::sign``."""
    return hmac.new(secret.encode("utf-8"), _canonical(body), hashlib.sha256).hexdigest()


def verify_hmac(secret, message):
    """True if ``message``'s ``hmac`` field matches its body, recomputed in
    constant time. The ``hmac`` field itself is excluded from the signed body.

    Mirrors ``frameassist/hostlink.py::verify``.
    """
    got = message.get("hmac")
    if not isinstance(got, str):
        return False
    body = {k: v for k, v in message.items() if k != "hmac"}
    return hmac.compare_digest(got, sign(secret, body))


# -- pure request handling (no ctypes, no sockets: unit-testable) -------------
def handle_line(secret, state, line):
    """Validate one wire line and decide what (if anything) to inject.

    This is the injection-free, ctypes-free heart of the protocol so it can be
    unit-tested cross-platform (P2T2). It does NOT inject; it returns a tuple
    ``(reply, action)`` where:

      * ``reply`` is the dict to send back — ``{"ok": True}`` on success or
        ``{"ok": False, "error": <message>}`` on any rejection/failure; and
      * ``action`` is ``(op, text)`` when the caller should call
        ``inject(op, text)``, or ``None`` when nothing should be injected.

    ``state`` is a small mutable dict held for the lifetime of one connection;
    it tracks ``last_seq`` (initialised to -1 so the first request's seq of 0 is
    accepted). A request is accepted only if it parses, its HMAC verifies, and
    its ``seq`` strictly increases; only then is ``last_seq`` advanced.
    """
    try:
        message = json.loads(line)
    except ValueError:
        return {"ok": False, "error": "bad json"}, None
    if not isinstance(message, dict):
        return {"ok": False, "error": "not a message"}, None

    # Authenticate before trusting anything in the body (esp. seq).
    if not verify_hmac(secret, message):
        return {"ok": False, "error": "bad hmac"}, None

    seq = message.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return {"ok": False, "error": "bad seq"}, None
    if seq <= state.get("last_seq", -1):
        return {"ok": False, "error": "stale seq"}, None

    op = message.get("op")
    if op not in OPS:
        # Authenticated and in-order, so advance seq, but there is nothing to do.
        state["last_seq"] = seq
        return {"ok": False, "error": "unknown op"}, None

    text = message.get("text", "")
    if op in _TEXT_OPS and not isinstance(text, str):
        state["last_seq"] = seq
        return {"ok": False, "error": "text required"}, None

    # Accepted: advance the per-connection counter and hand back the action.
    state["last_seq"] = seq
    return {"ok": True}, (op, text)


# -- Win32 injection (thin seam; NOT exercised in automated tests) ------------
#
# Everything below touches user32/kernel32 through ctypes. It is kept behind the
# single ``inject(op, text)`` boundary so the protocol logic above stays pure.
# Real injection runs only in manual end-to-end testing; P2T2 spies on
# ``inject`` rather than calling into it.
import ctypes  # noqa: E402  (imported here to keep the pure section above clean)

# Windows LLP64 type sizes; the companion runs only on Windows, where c_ulong /
# c_long are 4 bytes and c_void_p is pointer-sized. Defined from ctypes
# primitives (not ctypes.wintypes) so the module imports cleanly anywhere.
_WORD = ctypes.c_ushort
_DWORD = ctypes.c_ulong
_LONG = ctypes.c_long
_UINT = ctypes.c_uint
_ULONG_PTR = ctypes.c_void_p

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_BACK = 0x08
VK_CONTROL = 0x11
VK_V = 0x56

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class _MOUSEINPUT(ctypes.Structure):
    # Included only so the INPUT union is sized exactly like the OS's; we never
    # populate it. Without it sizeof(INPUT) is wrong and SendInput fails with
    # ERROR_INVALID_SIZE on 64-bit.
    _fields_ = [
        ("dx", _LONG),
        ("dy", _LONG),
        ("mouseData", _DWORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", _WORD),
        ("wScan", _WORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", _DWORD),
        ("wParamL", _WORD),
        ("wParamH", _WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", _DWORD), ("u", _INPUTUNION)]


def _user32():
    return ctypes.windll.user32


def _kernel32():
    return ctypes.windll.kernel32


def _utf16_units(text):
    """Yield each UTF-16 code unit of ``text`` (so astral characters such as
    emoji arrive as their surrogate pair, exactly as KEYEVENTF_UNICODE wants)."""
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        yield int.from_bytes(data[i:i + 2], "little")


def _send_keyinput(user32, vk, scan, flags):
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = scan
    inp.u.ki.dwFlags = flags
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = 0
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _type_unicode(user32, text):
    """Type ``text`` layout-independently: for each UTF-16 code unit send a
    KEYEVENTF_UNICODE keydown then its keyup. Accents, non-Latin scripts and
    emoji all land verbatim — the US-keyboard-layout limit is gone here."""
    for unit in _utf16_units(text):
        _send_keyinput(user32, 0, unit, KEYEVENTF_UNICODE)
        _send_keyinput(user32, 0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def _tap_vk(user32, vk, extended=False):
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    _send_keyinput(user32, vk, 0, flags)
    _send_keyinput(user32, vk, 0, flags | KEYEVENTF_KEYUP)


def _paste(user32):
    """Ctrl+V against the current foreground window."""
    _send_keyinput(user32, VK_CONTROL, 0, 0)
    _send_keyinput(user32, VK_V, 0, 0)
    _send_keyinput(user32, VK_V, 0, KEYEVENTF_KEYUP)
    _send_keyinput(user32, VK_CONTROL, 0, KEYEVENTF_KEYUP)


def _set_clipboard(text):
    """Put ``text`` on this PC's clipboard as CF_UNICODETEXT.

    ``GlobalAlloc``/``GlobalLock`` return pointers, so their restype/argtypes are
    set explicitly — without that ctypes truncates the handle to 32 bits on
    64-bit Windows and corrupts the clipboard block. After ``SetClipboardData``
    the block is owned by the clipboard, so it is deliberately not freed.
    """
    user32 = _user32()
    kernel32 = _kernel32()
    kernel32.GlobalAlloc.restype = _ULONG_PTR
    kernel32.GlobalAlloc.argtypes = [_UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [_ULONG_PTR]
    kernel32.GlobalUnlock.argtypes = [_ULONG_PTR]
    user32.SetClipboardData.restype = _ULONG_PTR
    user32.SetClipboardData.argtypes = [_UINT, _ULONG_PTR]

    data = (text + "\0").encode("utf-16-le")  # CF_UNICODETEXT is NUL-terminated
    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise OSError("GlobalLock failed")
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise OSError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def inject(op, text):
    """The single injection seam. Routes an accepted op to Win32.

    ``ping`` is a no-op. This is the ONLY place that touches user32/kernel32, so
    tests can replace it wholesale.
    """
    if op == "ping":
        return
    user32 = _user32()
    if op == "type":
        _type_unicode(user32, text)
    elif op == "enter":
        _tap_vk(user32, VK_RETURN)
    elif op == "escape":
        _tap_vk(user32, VK_ESCAPE)
    elif op == "backspace":
        _tap_vk(user32, VK_BACK)
    elif op == "paste":
        _paste(user32)
    elif op == "copy":
        _set_clipboard(text)
    else:
        raise ValueError("unknown op: %r" % (op,))


# -- connection serving -------------------------------------------------------
def _send(sock, message):
    sock.sendall(json.dumps(message).encode("utf-8") + b"\n")


def _serve(conn, secret):
    """One reader thread per connection: read lines, validate, inject, reply."""
    state = {"last_seq": -1}
    try:
        conn.settimeout(120)
        stream = conn.makefile("rb")
        while True:
            line = stream.readline(1 << 20)  # same cap as the headset side
            if not line:
                break
            reply, action = handle_line(secret, state, line)
            if action is not None:
                op, text = action
                try:
                    inject(op, text)
                except Exception as exc:  # never let one bad op kill the thread
                    reply = {"ok": False, "error": "inject failed: %s" % (exc,)}
            _send(conn, reply)
    except OSError:
        pass  # the usual ending: the headset closed the connection
    finally:
        try:
            conn.close()
        except OSError:
            pass


# -- config + pairing code ----------------------------------------------------
def config_dir():
    base = os.environ.get("APPDATA")
    if base:
        return os.path.join(base, "FrameAssistHost")
    return os.path.join(os.path.expanduser("~"), ".frameassisthost")


def new_pairing_code():
    """A fresh ~40-bit, human-typeable pairing code, e.g. ``K7QF-3MZX``.

    Eight characters from a 32-symbol unambiguous alphabet carry exactly 40
    bits. Derived from ``os.urandom`` (never the pairing secret on the wire).
    """
    bits = int.from_bytes(os.urandom(5), "big")  # 40 bits
    chars = [_CODE_ALPHABET[(bits >> (5 * (7 - i))) & 0x1F] for i in range(8)]
    return "".join(chars[:4]) + "-" + "".join(chars[4:])


def load_or_create_config():
    """Load ``%APPDATA%\\FrameAssistHost\\config.json`` or create it with a new
    pairing code, then return ``{"port": int, "secret": str}``."""
    folder = config_dir()
    path = os.path.join(folder, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict) and "port" in saved and "secret" in saved:
            return {"port": int(saved["port"]), "secret": str(saved["secret"])}
    except (OSError, ValueError):
        pass
    cfg = {"port": DEFAULT_PORT, "secret": new_pairing_code()}
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        # Can't persist (read-only profile, etc.): still run with this session's
        # code; the headset user just re-pairs next launch.
        pass
    return cfg


def lan_ipv4s():
    """The machine's non-loopback IPv4 address(es), for printing to the user."""
    addrs = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    if not addrs:
        # Fallback: whatever source address the OS would use to reach the LAN.
        # No packets are sent for a UDP "connect".
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            addrs.append(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()
    return addrs


def _setup_stdout():
    """Make prints visible immediately.

    When stdout is a redirected file/pipe (CI, or a helper spawned by something
    else) Python buffers it, so a force-killed process loses its banner. With
    line buffering every print appears as it happens — required both for the
    CI smoke test and for telling the user what the helper printed.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(line_buffering=True)
            except Exception:
                pass


def main():
    _setup_stdout()
    cfg = load_or_create_config()
    port = cfg["port"]
    secret = cfg["secret"]

    addrs = lan_ipv4s() or ["<this PC's LAN address>"]
    print("FrameAssist PC helper")
    print("---------------------")
    for ip in addrs:
        print("  PC address : %s:%d" % (ip, port))
    print("  Pairing code : %s" % secret)
    print()
    print("Enter these into the FrameAssist headset under Settings >")
    print("'Type into: A streamed PC'. Keep THIS WINDOW MINIMISED or in the")
    print("tray: SendInput types into whatever window is focused, so if this")
    print("console were focused it would type into itself. Never foreground it.")
    print()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("0.0.0.0", port))
    except OSError as exc:
        print("Could not bind port %d: %s" % (port, exc))
        return 1
    server.listen(4)
    print("Listening on 0.0.0.0:%d. Leave this running. Press Ctrl+C to stop." % port)

    # A short accept timeout returns the main thread to the interpreter between
    # accepts, so Ctrl+C (KeyboardInterrupt) is actually delivered on Windows;
    # without one, a thread parked in accept() never sees the signal.
    server.settimeout(0.5)
    try:
        while True:
            try:
                conn, _addr = server.accept()
            except TimeoutError:
                continue
            threading.Thread(target=_serve, args=(conn, secret), daemon=True).start()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    # A cancelled/broken startup must never silently flash-close the console:
    # print what happened and wait for a keypress, so the user can read it and
    # report it. A healthy run never reaches here (it blocks in accept()).
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    if rc != 0:
        print("\n(frameassist-host) The PC helper could not start (see the "
              "message above). Press Enter to close this window.")
        try:
            input()
        except EOFError:
            pass
    sys.exit(rc)

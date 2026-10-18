"""Headless tests for the streamed-PC LAN client (frameassist.hostlink).

A throwaway localhost TCP server speaks the same JSON-line + HMAC protocol the
Windows companion will, so the whole headset-side transport is exercised on a
dev box with no Windows and no real helper.
"""

import socket
import threading
import time

import pytest

from frameassist import hostlink


class FakeHost:
    """A minimal PC-helper stand-in: verifies the HMAC, records valid ops,
    replies {"ok": true} (or {"ok": false} when the HMAC doesn't check)."""

    def __init__(self, secret, *, always_ok=False):
        self.secret = secret
        self.always_ok = always_ok  # ignore HMAC (to observe the wire itself)
        self.received = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            conn.settimeout(3)
            stream = conn.makefile("rb")
            while not self._stop.is_set():
                try:
                    msg = hostlink.receive(stream)
                except (OSError, ValueError):
                    return
                if self.always_ok or hostlink.verify(self.secret, msg):
                    self.received.append(msg)
                    hostlink.send(conn, {"ok": True})
                else:
                    hostlink.send(conn, {"ok": False, "error": "bad hmac"})
                    return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


def _closed_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def fake_host():
    host = FakeHost("RIGHT-SECRET")
    try:
        yield host
    finally:
        host.close()


# -- HMAC scheme -------------------------------------------------------------

def test_host_unavailable_is_oserror():
    assert issubclass(hostlink.HostUnavailable, OSError)


def test_sign_verify_round_trip():
    body = {"v": 1, "op": "type", "text": "hi", "seq": 0}
    good = dict(body, hmac=hostlink.sign("s3cret", body))
    assert hostlink.verify("s3cret", good)
    # wrong secret is rejected
    assert not hostlink.verify("nope", good)
    # a tampered body (text changed after signing) is rejected
    tampered = dict(body, text="evil", hmac=good["hmac"])
    assert not hostlink.verify("s3cret", tampered)
    # a missing / non-string hmac is rejected, not crashed on
    assert not hostlink.verify("s3cret", dict(body))
    assert not hostlink.verify("s3cret", dict(body, hmac=None))


def test_canonical_is_stable_and_key_order_independent():
    a = hostlink._canonical({"b": 1, "a": 2})
    b = hostlink._canonical({"a": 2, "b": 1})
    assert a == b  # sort_keys makes the digest independent of insertion order


# -- end-to-end over a throwaway server --------------------------------------

def test_type_text_unicode_arrives_intact(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    t.type_text("héllo 🎉")  # accents + emoji: must survive verbatim (no keymap)
    assert len(fake_host.received) == 1
    msg = fake_host.received[0]
    assert msg["op"] == "type"
    assert msg["text"] == "héllo 🎉"
    t.close()


def test_shortcut_maps_to_ops(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    t.shortcut("enter")
    t.shortcut("paste")
    t.shortcut("escape")
    t.shortcut("backspace")
    assert [m["op"] for m in fake_host.received] == ["enter", "paste", "escape", "backspace"]
    t.close()


def test_shortcut_unknown_name_raises_keyerror(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    with pytest.raises(KeyError):
        t.shortcut("no-such-gesture")
    t.close()


def test_copy_text_is_copy_op(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    t.copy_text("clipboard contents")
    assert fake_host.received[0]["op"] == "copy"
    assert fake_host.received[0]["text"] == "clipboard contents"
    t.close()


def test_seq_is_monotonic_per_connection(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    t.type_text("one")
    t.type_text("two")
    t.type_text("three")
    assert [m["seq"] for m in fake_host.received] == [0, 1, 2]
    t.close()


def test_wrong_secret_rejected(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "WRONG-SECRET", timeout=2.0)
    with pytest.raises(hostlink.HostUnavailable):
        t.type_text("hi")
    # the helper verified and refused: nothing was accepted
    assert fake_host.received == []
    t.close()


def test_tampered_body_rejected_at_the_wire(fake_host):
    # Craft a message whose hmac does not match its body and send it raw.
    port = fake_host.port
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
        hostlink.send(sock, {"v": 1, "op": "type", "text": "evil", "seq": 0, "hmac": "deadbeef"})
        reply = hostlink.receive(sock.makefile("rb"))
    assert reply.get("ok") is False
    assert fake_host.received == []


def test_connection_refused_raises_host_unavailable_quickly():
    port = _closed_port()
    t = hostlink.HostTypist("127.0.0.1", port, "s", timeout=1.0)
    started = time.monotonic()
    with pytest.raises(hostlink.HostUnavailable):
        t.type_text("hi")
    assert time.monotonic() - started < 2.0  # fails fast, does not hang the caller


def test_ping_true_against_good_server(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    assert t.ping() is True
    t.close()


def test_ping_false_when_unreachable():
    t = hostlink.HostTypist("127.0.0.1", _closed_port(), "s", timeout=1.0)
    assert t.ping() is False


def test_ping_false_on_wrong_secret(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "WRONG", timeout=2.0)
    assert t.ping() is False


def test_reconnects_after_a_failure(fake_host):
    t = hostlink.HostTypist("127.0.0.1", fake_host.port, "RIGHT-SECRET", timeout=2.0)
    t.type_text("first")
    # force the connection down, as a dropped link would
    t._drop()
    t.type_text("second")  # must transparently reconnect
    texts = [m.get("text") for m in fake_host.received]
    assert texts == ["first", "second"]
    t.close()

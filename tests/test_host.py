"""Headless tests for the Phase 2 Windows companion (host/frameassist_host.py).

The companion is a standalone one-file script, so it is loaded by path with
importlib (NOT imported as part of the frameassist package). The headset-side
client (frameassist.hostlink.HostTypist) is the *other* independent
implementation and is used to prove cross-implementation wire interop over real
loopback TCP. Real SendInput/ctypes is never executed: ``inject`` is always
stubbed with a spy, and ``_user32`` is guarded to raise if anything ever tries
to reach Win32.
"""

import hashlib
import hmac
import importlib.util
import json
import re
import socket
import threading
from pathlib import Path

import pytest

from frameassist import hostlink
from frameassist.hostlink import HostTypist

# -- load the companion by path (it is not a package module) ------------------
HOST_PATH = Path(__file__).resolve().parent.parent / "host" / "frameassist_host.py"


def _load_companion():
    spec = importlib.util.spec_from_file_location("frameassist_host_under_test", HOST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = _load_companion()


def _sign(secret, body):
    """Independent signer for building wire messages in tests (same documented
    canonicalisation; used only to construct requests, never to assert the
    fixed vector)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _line(secret, **body):
    return json.dumps(dict(body, hmac=_sign(secret, body)))


# -- the companion is a standalone script, not part of the package ------------

def test_companion_is_standalone_loaded_by_path():
    # Loaded by file path (importlib), NOT imported as a package module...
    assert HOST_PATH.is_file()
    assert Path(host.__file__).resolve() == HOST_PATH
    # ...and its source never imports the frameassist package, so it is a truly
    # INDEPENDENT implementation -- the basis of the cross-implementation interop
    # proof below (audit finding F2). Mentions in prose/docstrings are allowed;
    # only a real `import frameassist` / `from frameassist` statement would fail.
    src = HOST_PATH.read_text(encoding="utf-8")
    assert re.search(r"^[ \t]*(?:import|from)[ \t]+frameassist\b", src, re.M) is None


# -- FIXED-VECTOR HMAC (closes audit finding F2) ------------------------------
# Computed ONCE from a reference (raw stdlib hmac over the exact canonical
# string), then hard-coded here as an independent constant. It is NOT recomputed
# from frameassist.hostlink at runtime — that is the whole point: it pins the
# canonicalisation contract so two independent implementations cannot drift.
VECTOR_SECRET = "ABCD-1234"
VECTOR_BODY = {"v": 1, "op": "type", "seq": 5, "text": "h\u00e9llo \U0001F389"}
VECTOR_CANONICAL = '{"op":"type","seq":5,"text":"h\\u00e9llo \\ud83c\\udf89","v":1}'
VECTOR_DIGEST = "d23a3692248977e20d917edc5855322c720e3e20ac70ddbe9e2081f035f9bd58"


def test_fixed_vector_pins_hostlink_sign():
    # The headset client's signer must match the frozen reference digest.
    assert hostlink.sign(VECTOR_SECRET, VECTOR_BODY) == VECTOR_DIGEST


def test_fixed_vector_canonical_shape():
    # The canonical serialisation itself is pinned (key order + separators +
    # ensure_ascii), independent of any implementation.
    canonical = json.dumps(VECTOR_BODY, sort_keys=True, separators=(",", ":"))
    assert canonical == VECTOR_CANONICAL


def test_fixed_vector_companion_agrees():
    # The companion's own verifier accepts exactly this vector's digest...
    good = dict(VECTOR_BODY, hmac=VECTOR_DIGEST)
    assert host.verify_hmac(VECTOR_SECRET, good) is True
    # ...and the pure handler accepts it and dispatches the right action.
    reply, action = host.handle_line(VECTOR_SECRET, {"last_seq": -1}, json.dumps(good))
    assert reply == {"ok": True}
    assert action == ("type", "h\u00e9llo \U0001F389")
    # A wrong digest for the same body is rejected by the companion too.
    bad = dict(VECTOR_BODY, hmac="00" * 32)
    assert host.verify_hmac(VECTOR_SECRET, bad) is False


# -- handle_line dispatch ------------------------------------------------------

@pytest.mark.parametrize("op", ["enter", "escape", "paste", "backspace", "ping"])
def test_handle_line_shortcut_ops(op):
    secret = "S3CRET"
    line = _line(secret, v=1, op=op, seq=0)
    reply, action = host.handle_line(secret, {"last_seq": -1}, line)
    assert reply == {"ok": True}
    assert action == (op, "")


def test_handle_line_type_verbatim_unicode():
    secret = "S3CRET"
    line = _line(secret, v=1, op="type", seq=0, text="h\u00e9llo \U0001F389")
    reply, action = host.handle_line(secret, {"last_seq": -1}, line)
    assert reply == {"ok": True}
    assert action == ("type", "h\u00e9llo \U0001F389")  # verbatim, no mangling


def test_handle_line_copy_carries_text():
    secret = "S3CRET"
    line = _line(secret, v=1, op="copy", seq=0, text="clipboard contents")
    reply, action = host.handle_line(secret, {"last_seq": -1}, line)
    assert action == ("copy", "clipboard contents")


def test_handle_line_unknown_op_rejected():
    secret = "S3CRET"
    line = _line(secret, v=1, op="selfdestruct", seq=0)
    reply, action = host.handle_line(secret, {"last_seq": -1}, line)
    assert reply["ok"] is False and action is None


def test_handle_line_bad_json_and_non_dict():
    secret = "S3CRET"
    reply, action = host.handle_line(secret, {"last_seq": -1}, "not json")
    assert reply["ok"] is False and action is None
    reply, action = host.handle_line(secret, {"last_seq": -1}, "[1, 2, 3]")
    assert reply["ok"] is False and action is None


# -- security ------------------------------------------------------------------

def test_wrong_secret_rejected_nothing_injected():
    secret = "S3CRET"
    line = _line(secret, v=1, op="type", seq=0, text="hi")
    reply, action = host.handle_line("WRONG-SECRET", {"last_seq": -1}, line)
    assert reply["ok"] is False
    assert action is None  # nothing to inject


def test_tampered_body_rejected():
    secret = "S3CRET"
    body = {"v": 1, "op": "type", "seq": 0, "text": "please type this"}
    msg = dict(body, hmac=_sign(secret, body))
    msg["text"] = "evil payload"  # changed AFTER signing
    reply, action = host.handle_line(secret, {"last_seq": -1}, json.dumps(msg))
    assert reply["ok"] is False and action is None


def test_replayed_seq_rejected_same_connection():
    secret = "S3CRET"
    state = {"last_seq": -1}
    line = _line(secret, v=1, op="type", seq=0, text="first")
    reply, action = host.handle_line(secret, state, line)
    assert reply == {"ok": True}
    # same seq again on the same connection -> rejected, nothing injected
    reply, action = host.handle_line(secret, state, line)
    assert reply["ok"] is False and action is None
    # a lower seq is also rejected
    lower = _line(secret, v=1, op="type", seq=0, text="first")
    reply, action = host.handle_line(secret, {"last_seq": 5}, lower)
    assert reply["ok"] is False and action is None


def test_secret_never_echoed_in_replies():
    secret = "SUP3R-SECR3T"
    replies = []
    good = _line(secret, v=1, op="type", seq=0, text="hi")
    replies.append(host.handle_line(secret, {"last_seq": -1}, good)[0])
    replies.append(host.handle_line("nope", {"last_seq": -1}, good)[0])
    replies.append(host.handle_line(secret, {"last_seq": -1}, "bad json")[0])
    replies.append(host.handle_line(secret, {"last_seq": -1},
                                    _line(secret, v=1, op="weird", seq=0))[0])
    for r in replies:
        assert secret not in json.dumps(r)


# -- config load-or-create -----------------------------------------------------

def test_config_first_call_creates_then_reuses(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "config_dir", lambda: str(tmp_path))
    cfg1 = host.load_or_create_config()
    assert cfg1["port"] == host.DEFAULT_PORT == 8763
    assert isinstance(cfg1["secret"], str) and cfg1["secret"]
    assert (tmp_path / "config.json").exists()
    # second load reuses the existing secret + port (no regeneration)
    cfg2 = host.load_or_create_config()
    assert cfg2 == cfg1


def test_config_reuses_preexisting_file(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "config_dir", lambda: str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"port": 9999, "secret": "PRE-EXIST"}), encoding="utf-8")
    cfg = host.load_or_create_config()
    assert cfg == {"port": 9999, "secret": "PRE-EXIST"}


def test_pairing_code_shape():
    code = host.new_pairing_code()
    assert len(code) == 9 and code[4] == "-"
    assert all(c in host._CODE_ALPHABET or c == "-" for c in code)


# -- loopback interop: real client vs. this independent companion --------------

@pytest.fixture
def companion_server(monkeypatch):
    """Run the REAL companion serving logic on 127.0.0.1 with inject spied and
    _user32 guarded, so no Win32 is ever touched."""
    calls = []

    def spy_inject(op, text):
        calls.append((op, text))

    def boom():
        raise AssertionError("Win32 user32 must never be reached in tests")

    monkeypatch.setattr(host, "inject", spy_inject)
    monkeypatch.setattr(host, "_user32", boom)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(4)

    stop = threading.Event()

    def accept_loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=host._serve, args=(conn, SERVER_SECRET),
                             daemon=True).start()

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    try:
        yield port, calls
    finally:
        stop.set()
        srv.close()


SERVER_SECRET = "PAIR-CODE"


def test_loopback_interop_client_to_companion(companion_server):
    port, calls = companion_server
    client = HostTypist("127.0.0.1", port, SERVER_SECRET, timeout=3.0)
    try:
        client.type_text("h\u00e9llo \U0001F389")
        assert client.ping() is True
        client.copy_text("clip")
        client.shortcut("enter")
        client.shortcut("paste")
    finally:
        client.close()
    # The spy received exactly the expected ops, in order — the headset client
    # and this independent companion agree on the wire (F2's cross-impl goal).
    assert calls == [
        ("type", "h\u00e9llo \U0001F389"),
        ("ping", ""),
        ("copy", "clip"),
        ("enter", ""),
        ("paste", ""),
    ]


def test_loopback_wrong_secret_is_rejected_by_companion(companion_server):
    port, calls = companion_server
    client = HostTypist("127.0.0.1", port, "WRONG-CODE", timeout=3.0)
    try:
        with pytest.raises(hostlink.HostUnavailable):
            client.type_text("should never inject")
    finally:
        client.close()
    assert calls == []  # companion verified and refused; nothing injected


def test_user32_guard_is_never_triggered(companion_server):
    # Drives the full happy path; if any inject reached _user32 the fixture's
    # guard would have raised. Reaching here proves SendInput was never called.
    port, calls = companion_server
    client = HostTypist("127.0.0.1", port, SERVER_SECRET, timeout=3.0)
    try:
        client.type_text("x")
    finally:
        client.close()
    assert calls == [("type", "x")]

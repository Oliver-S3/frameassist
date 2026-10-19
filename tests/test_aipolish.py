"""Tests for the AI polish HTTP layer (network mocked)."""

import json

from frameassist import aipolish


def test_polish_returns_original_without_key():
    assert aipolish.polish("text", "") == "text"
    assert aipolish.polish("", "key") == ""


def test_polish_sends_and_reads(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "Cleaned!"}}]}).encode()

    def fake_urlopen(req, timeout=20):
        captured["body"] = json.loads(req.data)
        captured["headers"] = dict(req.headers)
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = aipolish.polish("say  it  fast", "sk-or-test")
    assert out == "Cleaned!"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test"
    assert captured["body"]["model"].startswith("groq/")


def test_polish_returns_original_on_network_error(monkeypatch):
    def boom(req, timeout=20):
        raise ConnectionError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert aipolish.polish("keep me", "sk-or-test") == "keep me"


def test_polish_returns_original_on_bad_body(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"not json"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    assert aipolish.polish("keep me", "sk-or-test") == "keep me"


# -- local OpenAI-compatible endpoints (Ollama / LM Studio / llama.cpp) ---------

def _json_resp(text="Cleaned!"):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": text}}]}).encode()
    return R()


def test_endpoint_for_url_shaping():
    assert aipolish.endpoint_for("") == aipolish.DEFAULT_ENDPOINT
    assert aipolish.endpoint_for("http://localhost:11434/v1") == \
        "http://localhost:11434/v1/chat/completions"
    assert aipolish.endpoint_for("http://localhost:1234/v1/") == \
        "http://localhost:1234/v1/chat/completions"
    assert aipolish.endpoint_for("http://localhost:8080") == \
        "http://localhost:8080/v1/chat/completions"
    assert aipolish.endpoint_for("http://h:1/v1/chat/completions") == \
        "http://h:1/v1/chat/completions"


def test_polish_local_server_no_key(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=20):
        captured["url"] = req.full_url
        captured["auth"] = dict(req.headers).get("Authorization")
        captured["model"] = json.loads(req.data)["model"]
        return _json_resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = aipolish.polish("say  it  fast", "", model="qwen2.5:7b",
                          base_url="http://localhost:11434/v1")
    assert out == "Cleaned!"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["auth"] is None          # local servers don't need a key
    assert captured["model"] == "qwen2.5:7b"  # model override honoured


def test_polish_cloud_still_requires_key():
    # With no base URL, an empty key must not reach the network (kept as before).
    assert aipolish.polish("text", "") == "text"


def test_polish_local_network_error_keeps_original(monkeypatch):
    def boom(req, timeout=20):
        raise ConnectionError("refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert aipolish.polish("keep me", "", base_url="http://localhost:11434/v1") == "keep me"

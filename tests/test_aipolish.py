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

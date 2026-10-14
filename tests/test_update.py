"""Tests for in-app updates (network mocked)."""

import json
import subprocess

from frameassist import update


def test_version_tuple():
    assert update._version_tuple("1.2.3") == (1, 2, 3)
    assert update._version_tuple("2.0") == (2, 0)
    assert update._version_tuple("garbage") == (0, 0, 0)


def test_latest_version(monkeypatch):
    def fake(req, timeout=15):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"version": "2.0.0"}).encode()

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert update.latest_version("https://example.com/version.json") == "2.0.0"


def test_latest_version_none_on_error(monkeypatch):
    def boom(req, timeout=15):
        raise OSError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert update.latest_version("https://example.com/version.json") is None


def test_update_available_false_when_same(monkeypatch):
    monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
    monkeypatch.setattr(update, "DEFAULT_URL", "https://x/version.json")

    def fake(req, timeout=15):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"version": "1.0.0"}).encode()

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert update.update_available() is False


def test_run_self_update_up_to_date(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_URL", "https://x/version.json")
    monkeypatch.setattr(update, "current_version", lambda: "9.9.9")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _json_resp("9.9.9"))
    assert "up to date" in update.run_self_update().lower()


def test_run_self_update_rolls_back_on_failure(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_URL", "")
    monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
    calls = []

    def fake_pip(*args):
        calls.append(args)
        result = subprocess.CompletedProcess(args, returncode=0)
        return result

    def failing(result):
        # first call = install (make it return nonzero)
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"
        return R()

    calls.clear()
    original = update.subprocess.run
    results = [subprocess.CompletedProcess(["x"], returncode=1, stdout="", stderr="boom")]
    monkeypatch.setattr("frameassist.update.subprocess.run", lambda *a, **k: results.pop(0))
    msg = update.run_self_update()
    assert "failed" in msg.lower()


def _json_resp(text):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"version": text}).encode()

    return R()

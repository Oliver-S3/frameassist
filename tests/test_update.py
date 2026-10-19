"""Tests for in-app updates (network mocked)."""

import json
import subprocess

from frameassist import update


def test_version_tuple():
    assert update._version_tuple("1.2.3") == (1, 2, 3)
    assert update._version_tuple("2.0") == (2, 0)
    assert update._version_tuple("garbage") == (0, 0, 0)


# -- repo-based update source (FRAMEASSIST_UPDATE_BASE / BRANCH) ---------------

def test_update_source_uses_base_and_branch(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_BASE", "http://gitea/oliver/frameassist")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "main")
    check, pip = update.update_source()
    assert check == "http://gitea/oliver/frameassist/raw/main/version.json"
    assert pip == ["-U",
                   "frameassist @ http://gitea/oliver/frameassist/archive/main.tar.gz"]


def test_update_source_trailing_slash_and_custom_branch(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_BASE", "http://gitea/oliver/frameassist/")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "dev")
    check, pip = update.update_source()
    assert check == "http://gitea/oliver/frameassist/raw/dev/version.json"
    assert pip == ["-U", "frameassist @ http://gitea/oliver/frameassist/archive/dev.tar.gz"]


def test_update_source_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_BASE", "")
    monkeypatch.setattr(update, "DEFAULT_URL", "")
    assert update.update_source() == (None, None)


def test_update_source_github_base_uses_raw_and_codeload(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_BASE", "https://github.com/oliver-s3/frameassist")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "main")
    check, pip = update.update_source()
    assert check == "https://raw.githubusercontent.com/oliver-s3/frameassist/main/version.json"
    assert pip == ["-U",
                   "frameassist @ https://codeload.github.com/oliver-s3/frameassist/tar.gz/refs/heads/main"]


def test_update_source_github_trailing_git_and_custom_branch(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_BASE", "https://github.com/oliver-s3/frameassist.git")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "dev")
    check, pip = update.update_source()
    assert check == "https://raw.githubusercontent.com/oliver-s3/frameassist/dev/version.json"
    assert pip == ["-U",
                   "frameassist @ https://codeload.github.com/oliver-s3/frameassist/tar.gz/refs/heads/dev"]


def test_latest_version_uses_base_endpoint(monkeypatch):
    seen = {}

    def fake(req, timeout=15):
        seen["url"] = req
        return _json_resp("2.0.0")

    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr(update, "DEFAULT_BASE", "http://gitea/oliver/frameassist")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "main")
    assert update.latest_version() == "2.0.0"
    assert seen["url"] == "http://gitea/oliver/frameassist/raw/main/version.json"


def test_run_self_update_installs_from_archive(monkeypatch):
    captured = []

    def fake_run(*args, **kwargs):
        captured.append(args[0])
        return subprocess.CompletedProcess(args[0], returncode=0)

    monkeypatch.setattr("frameassist.update.subprocess.run", fake_run)
    monkeypatch.setattr(update, "DEFAULT_BASE", "http://gitea/oliver/frameassist")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "main")
    monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _json_resp("2.0.0"))
    msg = update.run_self_update()
    assert "updated" in msg.lower()
    assert any("-U" in c and "archive/main.tar.gz" in " ".join(c) for c in captured)


def test_run_self_update_reports_unreachable_source(monkeypatch):
    def boom(req, timeout=15):
        raise OSError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr(update, "DEFAULT_BASE", "http://gitea/oliver/frameassist")
    monkeypatch.setattr(update, "DEFAULT_BRANCH", "main")
    monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
    assert "couldn't reach" in update.run_self_update().lower()


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


def test_run_self_update_no_source_configured(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_URL", "")
    assert "no update source" in update.run_self_update().lower()


def test_run_self_update_rolls_back_on_failure(monkeypatch):
    monkeypatch.setattr(update, "DEFAULT_URL", "https://x/version.json")
    monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _json_resp("2.0.0"))
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

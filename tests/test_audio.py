"""Tests for the recorder: stop escalation and stray-process cleanup."""

import io
import signal
import subprocess

from frameassist import audio


class _StubbornProc:
    """A recorder that ignores every signal we send (wait always times out)."""

    def __init__(self):
        self.signals = []

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("pw-record", timeout or 0)


def test_stop_escalates_all_the_way_to_sigkill():
    rec = audio.Recorder()
    proc = _StubbornProc()
    rec._proc = proc
    rec._path = "/tmp/frameassist-test.wav"
    assert rec.stop() == "/tmp/frameassist-test.wav"
    assert rec._proc is None
    assert proc.signals == [signal.SIGINT, signal.SIGTERM, audio._SIGKILL]


def test_stop_returns_none_when_not_recording():
    rec = audio.Recorder()
    assert rec.stop() is None


def test_kill_stale_recorders_only_touches_our_own_recorders(monkeypatch):
    killed = []
    ours = str(audio.os.getpid())
    monkeypatch.setattr(audio.os, "listdir", lambda _p: ["101", "102", "103", ours])
    real_open = open

    def fake_open(path, mode="r", *args, **kwargs):
        if path == "/proc/101/cmdline":
            return io.BytesIO(b"pw-record\x00--rate\x0016000\x00/tmp/frameassist-abc.wav\x00")
        if path == "/proc/102/cmdline":
            return io.BytesIO(b"bash\x00")
        if path == "/proc/103/cmdline":
            return io.BytesIO(b"pw-record\x00/tmp/other-app.wav\x00")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(audio.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    audio.kill_stale_recorders()
    assert killed == [(101, signal.SIGTERM)]

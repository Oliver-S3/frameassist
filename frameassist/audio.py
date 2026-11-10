"""Record from the headset mic and load the audio for Whisper.

Recording uses PipeWire's pw-record when available (falling back to arecord).
Loading a WAV is done here rather than by faster-whisper's decoder, which
depends on PyAV and has broken with new PyAV releases.
"""

import os
import re
import shutil
import signal
import subprocess
import tempfile


# SIGKILL does not exist on Windows (tests import this module there); fall
# back to SIGTERM so the escalation loop stays importable everywhere.
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class Recorder:
    """Push-to-talk recorder. call start() to begin, stop() to finish."""

    def __init__(self):
        self._proc = None
        self._path = None

    @property
    def recording(self):
        return self._proc is not None

    def start(self):
        if self._proc:
            return
        fd, self._path = tempfile.mkstemp(suffix=".wav", prefix="frameassist-")
        os.close(fd)
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--rate", "16000", "--channels", "1", self._path]
        elif shutil.which("arecord"):
            cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", self._path]
        else:
            raise RuntimeError("No microphone recorder found (pw-record or arecord)")
        self._proc = subprocess.Popen(cmd)

    def cancel(self):
        path = self.stop()
        if path and os.path.exists(path):
            os.unlink(path)

    def stop(self):
        """Stop recording and return the path of the WAV file, or None."""
        if not self._proc:
            return None
        proc, self._proc = self._proc, None
        # SIGINT lets pw-record/arecord write the WAV header and exit; some
        # PipeWire builds ignore it, so escalate to SIGTERM and finally SIGKILL
        # (a recorder that outlives its take keeps the headset mic busy and
        # made "Listening…" look stuck forever — live 2026-11-10).
        for sig, wait_s in ((signal.SIGINT, 3), (signal.SIGTERM, 2),
                            (_SIGKILL, 2)):
            try:
                proc.send_signal(sig)
            except OSError:
                break
            try:
                proc.wait(timeout=wait_s)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return self._path


def kill_stale_recorders():
    """Kill recorder processes left behind by a previous FrameAssist run.

    A hard restart (or the app being killed) while a take was open used to
    orphan ``pw-record``: it kept writing a temp WAV and held the headset mic
    open, so the Frame showed "listening" forever and the next take could not
    capture. Run this once at startup, before any recording starts.
    """
    me = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        if ("pw-record" in cmd or "arecord" in cmd) and "frameassist-" in cmd:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def load_wav(path, rate=16000):
    """Read a PCM WAV as mono float32 at 16 kHz, the format Whisper expects."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        channels, width, src_rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit audio, got {8 * width}-bit")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if src_rate != rate and len(audio):
        n = int(len(audio) * rate / src_rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio.astype(np.float32)


# What Whisper "hears" in silence or breathing (it was trained on subtitles).
PHANTOM = {
    "thank you", "thanks for watching", "thank you for watching", "you", "bye",
    "thank you very much", "subtitles by the amara.org community",
}


def boost_quiet(audio, target=0.5, most=8.0):
    """Turn up a quiet recording (a headset mic held far off), so soft words
    aren't mistaken for silence. Loud recordings are left alone."""
    import numpy as np

    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak <= 1e-4 or peak >= target:
        return audio
    return (audio * min(target / peak, most)).astype(np.float32)


def keep(segment):
    """False for a segment that is almost surely not speech."""
    if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
        return False
    return re.sub(r"[^a-z. ]", "", segment.text.lower()).strip(" .") not in PHANTOM

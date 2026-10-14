"""Tests for the audio helpers that don't need a mic or Whisper model."""

import wave

import numpy as np
import pytest

from frameassist.audio import boost_quiet, keep


def _write_wav(path, samples, rate=16000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).astype("<i2").tobytes())


def test_boost_quiet_leaves_loud_alone():
    loud = np.full(1600, 0.7, dtype=np.float32)
    assert np.array_equal(boost_quiet(loud), loud)


def test_boost_quiet_turns_up_quiet():
    quiet = np.full(1600, 0.05, dtype=np.float32)
    boosted = boost_quiet(quiet)
    assert abs(boosted).max() > 0.1


def test_boost_quiet_outputs_float32():
    quiet = np.full(1600, 0.05, dtype=np.float32)
    assert boost_quiet(quiet).dtype == np.float32


def test_keep_phantom_is_dropped():
    class Seg:
        no_speech_prob = 0.9
        avg_logprob = -2.0
        text = "Thank you for watching"

    assert keep(Seg()) is False


def test_keep_speech_is_kept():
    class Seg:
        no_speech_prob = 0.1
        avg_logprob = -0.2
        text = "Buy the headphones"

    assert keep(Seg()) is True


def test_wav_stereo_is_mixed_to_mono(tmp_path):
    path = tmp_path / "s.wav"
    left = np.linspace(-1, 1, 8000, dtype=np.float32)
    right = np.zeros(8000, dtype=np.float32)
    stereo = np.stack([left, right], axis=1)
    _write_wav(path, stereo, channels=2)
    from frameassist.audio import load_wav
    mono = load_wav(str(path))
    assert mono.ndim == 1
    assert len(mono) == 8000
    assert mono.max() > 0.3

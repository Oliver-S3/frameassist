"""Speech-to-text: a local Whisper model via faster-whisper.

Everything runs on the headset's CPU (no cloud). ``faster_whisper`` is
imported lazily so the rest of the app starts even if it isn't installed yet.
"""

from .audio import boost_quiet, keep, load_wav


class Transcriber:
    def __init__(self, model="base.en", language="en"):
        from faster_whisper import WhisperModel

        # int8 on CPU keeps this fast enough on the headset's ARM chip.
        self._model = WhisperModel(model, device="cpu", compute_type="int8")
        self._language = language

    def transcribe(self, wav_path, words=""):
        return self.decode(load_wav(wav_path), words=words)

    def decode(self, audio, words="", beam=5, boost=True, gentle_vad=True,
               drop_phantoms=True):
        """Audio to text. words: the user's word list, comma separated ("" for
        none). """
        options = {}
        if words:
            # Names the user wants spelled their way (Settings > My words).
            options["hotwords"] = words
        if gentle_vad:
            # Cut silence, but keep a soft start or end of a word.
            options["vad_parameters"] = {
                "threshold": 0.35, "speech_pad_ms": 400,
                "min_silence_duration_ms": 1000,
            }
        segments, _ = self._model.transcribe(
            boost_quiet(audio) if boost else audio,
            language=self._language,
            beam_size=beam,
            # Each dictation stands alone; carrying text over can repeat words.
            condition_on_previous_text=not drop_phantoms,
            vad_filter=True,
            **options,
        )
        return " ".join(
            s.text.strip() for s in segments
            if not drop_phantoms or keep(s)
        ).strip()

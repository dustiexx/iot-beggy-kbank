"""Offline speech-to-text on the Pi (faster-whisper), the last backup when every Gemini STT model fails
(quota, 503, no internet).

Measured on the Pi 5 (int8, 4 threads) with ~2.7 s Thai questions:
    tiny   1.3 s  too many wrong words
    base   2.4 s  small tone/vowel slips ("ฟุ่น" for "ฝุ่น") that Gemini still understands   <- default
    small  7 s    not better than base for Thai
Thai fine-tuned models (biodatlab / Pathumma, medium / large) are much more accurate but too slow for
the Pi - better on a PC. WHISPER_MODEL takes a size ("base") or a local CTranslate2 model folder.
"""
from __future__ import annotations

import io
import logging
import threading
import wave

import numpy as np

log = logging.getLogger("local_stt")


class LocalWhisper:
    def __init__(self, model: str = "base", language: str = "th", threads: int = 4):
        self.model_name = model
        self.language = language
        self.threads = threads
        self._model = None
        self._lock = threading.Lock()   # one transcription at a time; also guards the lazy load

    @property
    def name(self) -> str:
        return f"whisper-{self.model_name.rstrip('/').split('/')[-1]}"

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("loading %s", self.name)
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=self.threads)
        return self._model

    def transcribe(self, wav: bytes) -> str:
        with wave.open(io.BytesIO(wav)) as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
                raise ValueError("local STT needs 16 kHz mono 16-bit wav")
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        with self._lock:
            segments, _ = self._load().transcribe(audio, language=self.language, beam_size=1,
                                                  condition_on_previous_text=False)
            return "".join(s.text for s in segments).strip()


def make_local_stt(model: str | None) -> LocalWhisper | None:
    """None when disabled (WHISPER_MODEL=off) or faster-whisper isn't installed."""
    if not model or model.lower() in ("off", "none", "0", "false"):
        return None
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        log.warning("faster-whisper not installed - no offline STT backup (pip install faster-whisper)")
        return None
    return LocalWhisper(model)

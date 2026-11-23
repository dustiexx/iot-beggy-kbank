"""Microphone input and speech recording helpers. Everything is 16 kHz mono int16."""
from __future__ import annotations

import io
import queue
import wave
from dataclasses import dataclass

import numpy as np
from scipy.signal import resample_poly


def find_input_device(name_part: str) -> int:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name_part.lower() in d["name"].lower():
            return i
    raise RuntimeError(f"no input device matching {name_part!r} - run `voice_main.py devices`")


class Mic:
    """Reads the USB mic at its native rate and yields 16 kHz int16 chunks of `chunk` samples."""

    def __init__(self, device: str, mic_rate: int, rate: int, chunk: int):
        import sounddevice as sd
        if mic_rate % rate:
            raise ValueError("mic_rate must be a multiple of rate")
        self.factor = mic_rate // rate
        self.chunk = chunk
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self.stream = sd.InputStream(
            device=find_input_device(device), samplerate=mic_rate, channels=1,
            dtype="int16", blocksize=chunk * self.factor, callback=self._cb)

    def _cb(self, indata, frames, time_info, status):
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # consumer is too slow; drop audio rather than block the audio thread

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def flush(self):
        while not self.q.empty():
            self.q.get_nowait()

    def read(self) -> np.ndarray:
        raw = self.q.get()
        return to_16k(raw, self.factor)


def to_16k(raw: np.ndarray, factor: int) -> np.ndarray:
    if factor == 1:
        return raw.astype(np.int16)
    y = resample_poly(raw.astype(np.float32), 1, factor)
    return np.clip(y, -32768, 32767).astype(np.int16)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if len(x) else 0.0


@dataclass
class Endpointer:
    """Decides when an utterance starts and ends from per-chunk loudness.

    Feed it chunks with `push`; it returns "waiting", "speaking", "done" or "timeout".
    """
    noise_floor: float
    chunk_s: float
    start_timeout_s: float
    end_silence_s: float
    max_record_s: float
    speech_factor: float
    min_speech_rms: float

    def __post_init__(self):
        self.t = 0.0
        self.started = False
        self.silence = 0.0

    @property
    def threshold(self) -> float:
        return max(self.noise_floor * self.speech_factor, self.min_speech_rms)

    def push(self, level: float) -> str:
        self.t += self.chunk_s
        loud = level >= self.threshold
        if not self.started:
            if loud:
                self.started = True
                return "speaking"
            return "timeout" if self.t >= self.start_timeout_s else "waiting"
        self.silence = 0.0 if loud else self.silence + self.chunk_s
        if self.silence >= self.end_silence_s or self.t >= self.max_record_s:
            return "done"
        return "speaking"


def to_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()

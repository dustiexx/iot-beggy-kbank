"""Text to speech with a fallback chain, so Beggy always says something:

  1. edge-tts  - free Microsoft neural Thai voices, best quality (~1 s). Unofficial service: it
                 sometimes refuses requests ("No audio was received", connection refused), so tight timeouts.
  2. gTTS      - free Google Translate voice, unofficial too, sometimes slow.
  3. espeak-ng - offline on the Pi, robotic but never fails.

AUDIO_OUT=none    -> no speaker yet: the audio is saved to tts_out/ (and its path logged)
AUDIO_OUT=default -> play on the default ALSA device
AUDIO_OUT=hw:3,0  -> play on that ALSA device
AUDIO_GAIN_DB=8   -> play louder (soft limiter, no hard clipping)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("tts")


def _edge(text: str, voice: str, timeout: float) -> bytes:
    import edge_tts

    async def run():
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)
    return asyncio.run(asyncio.wait_for(run(), timeout=timeout))


def _gtts(text: str, timeout: float) -> bytes:
    from gtts import gTTS
    buf = io.BytesIO()
    gTTS(text, lang="th", timeout=timeout).write_to_fp(buf)
    return buf.getvalue()


def _espeak(text: str) -> bytes:
    return subprocess.run(["espeak-ng", "-v", "th", "-s", "150", "--stdout", text],
                          capture_output=True, timeout=10, check=True).stdout


def soft_limit(x: np.ndarray, gain_db: float, knee: float = 0.7) -> np.ndarray:
    """int16 samples * gain; above `knee` (fraction of full scale) peaks are rounded off (tanh) instead of clipped."""
    y = x.astype(np.float32) / 32768 * 10 ** (gain_db / 20)
    a = np.abs(y)
    over = a > knee
    y[over] = np.sign(y[over]) * (knee + (1 - knee) * np.tanh((a[over] - knee) / (1 - knee)))
    return np.clip(np.round(y * 32768), -32768, 32767).astype(np.int16)


def boost_file(src: str, fmt: str, dst_wav: str, gain_db: float) -> None:
    """Decode (mp3 via mpg123) -> soft_limit -> 16-bit wav."""
    if fmt == "mp3":
        subprocess.run(["mpg123", "-q", "-w", dst_wav, src], check=True, timeout=30)
        src = dst_wav
    with wave.open(src) as r:
        params, frames = r.getparams(), r.readframes(r.getnframes())
    if params.sampwidth != 2:
        raise ValueError(f"{params.sampwidth * 8}-bit audio")
    with wave.open(dst_wav, "wb") as out:
        out.setparams(params)
        out.writeframes(soft_limit(np.frombuffer(frames, np.int16), gain_db).tobytes())


class Speaker:
    EDGE_PAUSE_S = 600
    def __init__(self, audio_out: str, out_dir: Path, edge_timeout: float = 3, gtts_timeout: float = 6,
                 gain_db: float = 0.0):
        self.audio_out = audio_out
        self.gain_db = gain_db                 # AUDIO_GAIN_DB: louder playback through a soft limiter
        self.edge_skip_until = 0.0             # monotonic time; edge-tts is skipped until then after a failure
        self.out_dir = out_dir
        self.edge_timeout = edge_timeout
        self.gtts_timeout = gtts_timeout

    def synth(self, text: str, voice: str) -> tuple[bytes, str, str]:
        """Returns (audio bytes, "mp3" | "wav", engine used).

        edge-tts (best voice) and gTTS run at the same time: edge-tts is used if it answers within
        edge_timeout, otherwise the gTTS audio is already there - no waiting for one to fail first."""
        pool = concurrent.futures.ThreadPoolExecutor(2)
        try:
            gtts_job = pool.submit(_gtts, text, self.gtts_timeout)
            if time.monotonic() >= self.edge_skip_until:
                edge_job = pool.submit(_edge, text, voice, self.edge_timeout)
                try:
                    data = edge_job.result(timeout=self.edge_timeout + 0.5)
                    if data:
                        return data, "mp3", "edge-tts"
                    raise RuntimeError("empty audio")
                except Exception as e:   # it tends to fail for a while: skip it for a bit
                    self.edge_skip_until = time.monotonic() + self.EDGE_PAUSE_S
                    log.warning("edge-tts failed (%s), using gTTS for the next %d min",
                                str(e)[:100] or type(e).__name__, self.EDGE_PAUSE_S // 60)
            try:
                return gtts_job.result(timeout=self.gtts_timeout + 1), "mp3", "gtts"
            except Exception as e:
                log.warning("gTTS failed: %s", str(e)[:100] or type(e).__name__)
            return _espeak(text), "wav", "espeak-ng"
        finally:
            pool.shutdown(wait=False)   # don't wait for the slower engine

    def say(self, text: str, voice: str) -> dict:
        t0 = time.time()
        audio, fmt, engine = self.synth(text, voice)
        info = {"engine": engine, "synth_s": round(time.time() - t0, 2)}
        if self.audio_out == "none":
            self.out_dir.mkdir(exist_ok=True)
            path = self.out_dir / f"reply_{int(time.time() * 1000)}.{fmt}"
            path.write_bytes(audio)
            info["saved"] = str(path)
            return info
        with tempfile.NamedTemporaryFile(suffix="." + fmt) as f, tempfile.NamedTemporaryFile(suffix=".wav") as w:
            f.write(audio)
            f.flush()
            if self.gain_db:
                try:
                    boost_file(f.name, fmt, w.name, self.gain_db)
                    f, fmt = w, "wav"
                except Exception as e:   # never lose the reply over the volume boost
                    log.warning("volume boost failed, playing as is: %s", str(e)[:100])
            if fmt == "mp3":
                cmd = ["mpg123", "-q"] + ([] if self.audio_out == "default" else ["-a", self.audio_out])
            else:
                cmd = ["aplay", "-q"] + ([] if self.audio_out == "default" else ["-D", self.audio_out])
            subprocess.run(cmd + [f.name], timeout=60, check=False)
        info["played_s"] = round(time.time() - t0, 2)
        return info

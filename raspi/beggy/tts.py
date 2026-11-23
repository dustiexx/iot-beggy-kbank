"""Text to speech with a fallback chain, so Beggy always says something:

  1. edge-tts  - free Microsoft neural Thai voices, best quality (~1 s). Unofficial service: it
                 sometimes refuses requests ("No audio was received", connection refused), so tight timeouts.
  2. gTTS      - free Google Translate voice, unofficial too, sometimes slow.
  3. espeak-ng - offline on the Pi, robotic but never fails.

AUDIO_OUT=none    -> no speaker yet: the audio is saved to tts_out/ (and its path logged)
AUDIO_OUT=default -> play on the default ALSA device
AUDIO_OUT=hw:3,0  -> play on that ALSA device
"""
from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import tempfile
import time
from pathlib import Path

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


class Speaker:
    def __init__(self, audio_out: str, out_dir: Path, edge_timeout: float = 6, gtts_timeout: float = 6):
        self.audio_out = audio_out
        self.out_dir = out_dir
        self.edge_timeout = edge_timeout
        self.gtts_timeout = gtts_timeout

    def synth(self, text: str, voice: str) -> tuple[bytes, str, str]:
        """Returns (audio bytes, "mp3" | "wav", engine used)."""
        for attempt in range(2):
            try:
                data = _edge(text, voice, self.edge_timeout)
                if data:
                    return data, "mp3", "edge-tts"
            except Exception as e:
                log.warning("edge-tts attempt %d failed: %s", attempt + 1, str(e)[:100])
        try:
            return _gtts(text, self.gtts_timeout), "mp3", "gtts"
        except Exception as e:
            log.warning("gTTS failed: %s", str(e)[:100])
        return _espeak(text), "wav", "espeak-ng"

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
        with tempfile.NamedTemporaryFile(suffix="." + fmt) as f:
            f.write(audio)
            f.flush()
            if fmt == "mp3":
                cmd = ["mpg123", "-q"] + ([] if self.audio_out == "default" else ["-a", self.audio_out])
            else:
                cmd = ["aplay", "-q"] + ([] if self.audio_out == "default" else ["-D", self.audio_out])
            subprocess.run(cmd + [f.name], timeout=60, check=False)
        info["played_s"] = round(time.time() - t0, 2)
        return info

#!/usr/bin/env python3
"""Beggy voice: wake word -> record what the user says -> Gemini (transcript + reply).

    python voice_main.py devices                 # list microphones
    python voice_main.py level                   # live mic loudness meter (check the mic works)
    python voice_main.py listen                  # say the wake word, then talk
    python voice_main.py listen --no-wake        # press Enter instead of the wake word
    python voice_main.py listen --dry-run        # skip Gemini, just save the clips

Prints one JSON object per line on stdout, like posture_main.py:
    {"type": "wake", ...} {"type": "recorded", ...} {"type": "voice_reply", "transcript": ..., "reply": ...}
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from voice.audio import Endpointer, Mic, rms, to_wav
from voice.config import VoiceConfig


def emit(obj: dict) -> None:
    obj.setdefault("unix_ms", int(time.time() * 1000))
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).with_name(".env"))
    except ImportError:
        pass


def cmd_devices(args, cfg) -> int:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = " <- used" if cfg.device.lower() in d["name"].lower() else ""
            print(f"{i}: {d['name']}  ({int(d['default_samplerate'])} Hz){mark}")
    return 0


def cmd_level(args, cfg) -> int:
    log("Talk into the mic - Ctrl+C to stop")
    with Mic(cfg.device, cfg.mic_rate, cfg.rate, cfg.chunk) as mic:
        try:
            while True:
                lvl = np.mean([rms(mic.read()) for _ in range(3)])
                bar = "#" * min(60, int(lvl / 100))
                print(f"\r{lvl:7.0f} {bar:<60}", end="", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            print(file=sys.stderr)
    return 0


def record_utterance(mic: Mic, cfg: VoiceConfig, noise_floor: float):
    ep = Endpointer(noise_floor, cfg.chunk / cfg.rate, cfg.start_timeout_s, cfg.end_silence_s,
                    cfg.max_record_s, cfg.speech_factor, cfg.min_speech_rms)
    pre = collections.deque(maxlen=4)   # keep ~0.3 s before speech starts so the first syllable isn't cut
    chunks: list[np.ndarray] = []
    while True:
        c = mic.read()
        state = ep.push(rms(c))
        if state == "waiting":
            pre.append(c)
        elif state == "timeout":
            return None
        else:
            if not chunks:
                chunks.extend(pre)
            chunks.append(c)
            if state == "done":
                return np.concatenate(chunks)


def cmd_listen(args, cfg: VoiceConfig) -> int:
    gemini = None
    if not args.dry_run:
        from voice.gemini import GeminiVoice
        try:
            gemini = GeminiVoice(cfg.model, cfg.system_prompt)
        except RuntimeError as e:
            log(f"{e}\n(use --dry-run to test recording without Gemini)")
            return 1

    wake = None
    if not args.no_wake:
        from voice.wake import make_wake
        wake = make_wake(cfg.wakeword)

    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    levels = collections.deque(maxlen=int(3 * cfg.rate / cfg.chunk))  # last ~3 s for the noise floor
    with Mic(cfg.device, cfg.mic_rate, cfg.rate, cfg.chunk) as mic:
        log(f"Listening for '{cfg.wakeword}'..." if wake else "Press Enter, then talk.")
        try:
            while True:
                # 1. wait for the trigger
                if wake:
                    c = mic.read()
                    levels.append(rms(c))
                    score = wake.predict(c)
                    if score < cfg.wake_threshold:
                        continue
                    emit({"type": "wake", "wakeword": cfg.wakeword, "score": round(float(score), 3)})
                else:
                    input()
                    mic.flush()
                    levels.extend(rms(mic.read()) for _ in range(5))
                    emit({"type": "wake", "wakeword": None, "score": None})

                # 2. record until the user stops talking
                noise = float(np.median(levels)) if levels else 100.0
                log("  recording... (talk now)")
                audio = record_utterance(mic, cfg, noise)
                if audio is None:
                    emit({"type": "no_speech"})
                    log("  heard nothing")
                else:
                    secs = len(audio) / cfg.rate
                    emit({"type": "recorded", "seconds": round(secs, 2)})
                    wav = to_wav(audio, cfg.rate)
                    if save_dir:
                        (save_dir / f"clip_{int(time.time())}.wav").write_bytes(wav)

                    # 3. Gemini: transcript + reply
                    if gemini:
                        log("  asking Gemini...")
                        t0 = time.time()
                        try:
                            r = gemini.ask(wav)
                            emit({"type": "voice_reply", "transcript": r.transcript, "reply": r.reply,
                                  "latency_s": round(time.time() - t0, 2)})
                        except Exception as e:  # network / quota errors shouldn't kill the loop
                            emit({"type": "voice_error", "error": str(e)[:300]})

                # back to waiting; drop audio queued while we were busy
                mic.flush()
                if wake:
                    wake.reset()
                    end = time.time() + cfg.wake_cooldown_s
                    while time.time() < end:
                        mic.read()
                log(f"Listening for '{cfg.wakeword}'..." if wake else "Press Enter, then talk.")
        except (KeyboardInterrupt, EOFError):
            pass
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Beggy voice: wake word -> Gemini")
    p.add_argument("command", choices=["devices", "level", "listen"])
    p.add_argument("--device", help="part of the mic name (default from config)")
    p.add_argument("--wakeword", help="openWakeWord model name (hey_jarvis, alexa...) or our models/hey_beggy.joblib")
    p.add_argument("--threshold", type=float, help="wake word score threshold (0..1)")
    p.add_argument("--no-wake", action="store_true", help="press Enter instead of saying the wake word")
    p.add_argument("--dry-run", action="store_true", help="don't call Gemini")
    p.add_argument("--save-dir", help="save each recorded clip as a .wav here")
    args = p.parse_args()

    load_env()
    cfg = VoiceConfig()
    if args.device:
        cfg.device = args.device
    if args.wakeword:
        cfg.wakeword = args.wakeword
    if args.threshold is not None:
        cfg.wake_threshold = args.threshold

    return {"devices": cmd_devices, "level": cmd_level, "listen": cmd_listen}[args.command](args, cfg)


if __name__ == "__main__":
    sys.exit(main())

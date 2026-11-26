#!/usr/bin/env python3
"""Train our own "hey Beggy" wake word on the Pi (CPU, a few minutes).

    python wakeword_train.py record-pos --n 40      # say "hey Beggy" 40 times (most important data)
    python wakeword_train.py record-neg --seconds 180   # talk normally / room noise, NOT the wake word
    python wakeword_train.py synth                  # robot voices (espeak-ng): positives + near misses
    python wakeword_train.py train                  # -> models/hey_beggy.joblib + wakeword_data/report.md
    python wakeword_train.py live                   # live score meter to try it

Then set WAKEWORD=models/hey_beggy.joblib (in .env or the dashboard settings `wakeword`).

How it works: openWakeWord's pretrained speech-embedding model turns the last ~2 s of audio into
16 x 96 features; we train a small MLP on those features to say "hey Beggy" or not.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from voice.audio import Endpointer, Mic, MicStalled, rms, to_wav
from voice.config import VoiceConfig

DATA = Path("wakeword_data")
POS, NEG = DATA / "positive", DATA / "negative"
MODEL_PATH = Path("models/hey_beggy.joblib")
SR, WIN = 16000, 32000            # 2 s windows -> 16 embedding frames

POS_TEXTS = ["hey beggy", "hey beggie", "hey beggy!", "hey, beggy", "เฮ้ เบกกี้", "เฮ เบ็กกี้"]
NEAR_MISS = ["hey baby", "hey buddy", "hey becky", "hey bakery", "beggar", "baggy", "begging", "hey jarvis",
             "hey bobby", "hey betty", "hey peggy", "a baby", "hey maybe", "hey daddy", "hey bae", "baby",
             "okay google", "hey siri", "hey", "beggy", "เบเกอรี่", "เป็ดกี่ตัว", "เฮ้ย", "เบื่อจัง", "เปิดไฟ"]
SENTENCES = ["what time is it", "I need to finish my homework", "let's get some coffee", "the meeting is at three",
             "วันนี้อากาศดีจัง", "ขอน้ำหน่อย", "เดี๋ยวไปกินข้าวกัน", "งานนี้ต้องส่งพรุ่งนี้", "ช่วยเปิดเพลงหน่อย",
             "เบกกี้เป็นหุ่นยนต์", "hello there how are you", "turn on the light please", "ฉันเหนื่อยมากเลย"]
ESPEAK_VOICES = ["en", "en-us", "en-gb-x-rp", "en-gb-scotland", "en-029", "en-us-nyc", "en-gb-x-gbclan"]
VARIANTS = ["", "+m1", "+m3", "+m5", "+m7", "+f1", "+f2", "+f3", "+f4", "+croak", "+klatt"]


# ------------------------------------------------------------------ audio helpers
def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels())[:, 0]
        rate = w.getframerate()
    if rate != SR:
        g = np.gcd(rate, SR)
        x = np.clip(resample_poly(x.astype(np.float32), SR // g, rate // g), -32768, 32767).astype(np.int16)
    return x


def write_wav(path: Path, x: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_wav(x, SR))


def trim(x: np.ndarray, frame: int = 320) -> np.ndarray:
    """Cut leading/trailing silence (energy below 15% of the loudest 20 ms)."""
    if len(x) < frame:
        return x
    e = np.array([rms(x[i:i + frame]) for i in range(0, len(x) - frame, frame)])
    on = np.where(e > 0.15 * e.max())[0]
    if not len(on):
        return x
    return x[max(0, on[0] * frame - 1600): min(len(x), (on[-1] + 1) * frame + 1600)]


def place(utt: np.ndarray, bg: np.ndarray | None, rng: random.Random) -> np.ndarray:
    """Put the utterance so it ends 0.05-0.35 s before the end of a 2 s window, over background."""
    utt = utt[-(WIN - 800):]
    end = WIN - rng.randint(800, 5600)
    start = max(0, end - len(utt))
    out = background(bg, rng, 0.05)
    out[start:start + len(utt)] += utt[: WIN - start].astype(np.float32)
    return np.clip(out, -32768, 32767).astype(np.int16)


def background(bg: np.ndarray | None, rng: random.Random, level: float) -> np.ndarray:
    if bg is not None and len(bg) > WIN:
        i = rng.randint(0, len(bg) - WIN)
        return bg[i:i + WIN].astype(np.float32) * rng.uniform(level, 0.6)
    return np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, 30, WIN).astype(np.float32)


REAL_NEG_EXTRA = 2   # extra augmented copies of each real negative window


def fit(x: np.ndarray) -> np.ndarray:
    """Cut or zero-pad to exactly one 2 s window (augment() changes the length)."""
    return x[:WIN] if len(x) >= WIN else np.pad(x, (0, WIN - len(x)))


def augment(x: np.ndarray, rng: random.Random) -> np.ndarray:
    y = x.astype(np.float32)
    s = rng.choice([0.9, 0.95, 1.0, 1.0, 1.05, 1.1])        # speed / pitch perturbation
    if s != 1.0:
        y = resample_poly(y, 100, int(100 * s))
    y *= 10 ** (rng.uniform(-8, 6) / 20)                     # loudness
    return np.clip(y, -32768, 32767).astype(np.int16)


# ------------------------------------------------------------------ data collection
def cmd_record_pos(args):
    cfg = VoiceConfig()
    POS.mkdir(parents=True, exist_ok=True)
    print(f"Say 'hey Beggy' when you see SAY NOW. Vary it: normal, quiet, fast, from further away. Ctrl+C to stop.")
    with Mic(cfg.device, cfg.mic_rate, cfg.rate, cfg.chunk) as mic:
        noise = float(np.median([rms(mic.read()) for _ in range(15)]))
        got = 0
        while got < args.n:
            mic.flush()
            print(f"  [{got + 1}/{args.n}] SAY NOW", flush=True)
            ep = Endpointer(noise, cfg.chunk / cfg.rate, 4.0, 0.5, 3.0, cfg.speech_factor, cfg.min_speech_rms)
            chunks, pre = [], []
            while True:
                c = mic.read()
                st = ep.push(rms(c))
                if st == "waiting":
                    pre = (pre + [c])[-4:]
                elif st == "timeout":
                    print("    (heard nothing, again)")
                    break
                else:
                    chunks = chunks or list(pre)
                    chunks.append(c)
                    if st == "done":
                        x = np.concatenate(chunks)
                        write_wav(POS / f"real_{int(time.time() * 1000)}.wav", x)
                        got += 1
                        print(f"    saved {len(x) / SR:.1f} s")
                        break
            time.sleep(0.4)
    print(f"{got} clips in {POS}/")


def cmd_record_neg(args):
    cfg = VoiceConfig()
    NEG.mkdir(parents=True, exist_ok=True)
    print(f"Recording {args.seconds:.0f} s of NOT-wake-word audio: talk normally, read something aloud, "
          f"type, play music... just don't say 'hey Beggy'.")
    with Mic(cfg.device, cfg.mic_rate, cfg.rate, cfg.chunk) as mic:
        buf, t_end, part = [], time.time() + args.seconds, 0
        while time.time() < t_end:
            try:
                buf.append(mic.read())
            except MicStalled as e:                     # keep what we have, don't hang
                print(f"  mic stopped ({e}) - saving what was recorded", flush=True)
                break
            if len(buf) * cfg.chunk >= 10 * SR:           # save in 10 s pieces
                write_wav(NEG / f"real_{int(time.time())}_{part}.wav", np.concatenate(buf))
                buf, part = [], part + 1
                print(f"  {t_end - time.time():4.0f} s left", flush=True)
        if buf:
            write_wav(NEG / f"real_{int(time.time())}_{part}.wav", np.concatenate(buf))
    print(f"saved to {NEG}/")


def espeak(text: str, voice: str, speed: int, pitch: int) -> np.ndarray:
    wav = subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-p", str(pitch), "--stdout", text],
                         capture_output=True, check=True).stdout
    tmp = DATA / "_tmp.wav"
    tmp.write_bytes(wav)
    return read_wav(tmp)


def cmd_synth(args):
    rng = random.Random(0)
    n_pos = n_neg = 0
    for i in range(args.n):
        txt = rng.choice(POS_TEXTS)
        voice = "th" if any("฀" <= ch <= "๿" for ch in txt) else rng.choice(ESPEAK_VOICES)
        x = espeak(txt, voice + rng.choice(VARIANTS), rng.randint(120, 210), rng.randint(25, 75))
        write_wav(POS / f"synth_{i:04d}.wav", x)
        n_pos += 1
    for i in range(args.n):
        # 2/3 near misses ("hey baby", "beggar"...): the hard cases the model must learn to reject
        txt = rng.choice(NEAR_MISS) if i % 3 else rng.choice(SENTENCES)
        voice = "th" if any("฀" <= ch <= "๿" for ch in txt) else rng.choice(ESPEAK_VOICES)
        x = espeak(txt, voice + rng.choice(VARIANTS), rng.randint(120, 210), rng.randint(25, 75))
        write_wav(NEG / f"synth_{i:04d}.wav", x)
        n_neg += 1
    (DATA / "_tmp.wav").unlink(missing_ok=True)
    print(f"synthesised {n_pos} positive and {n_neg} negative clips")


# ------------------------------------------------------------------ training
def build(files_pos, files_neg, bg, rng, aug_per_pos):
    X_pos, X_neg = [], []
    for f in files_pos:
        utt = trim(read_wav(f))
        for _ in range(aug_per_pos):
            X_pos.append(place(augment(utt, rng), bg, rng))
    for f in files_neg:
        x = read_wav(f)
        if len(x) <= WIN:                                   # short clip (synthetic phrase / near miss)
            for _ in range(aug_per_pos):                    # same augmentation as positives -> balanced
                X_neg.append(place(augment(trim(x), rng), bg, rng))
        else:                                               # long recording: sliding windows
            for i in range(0, len(x) - WIN, SR // 4):
                X_neg.append(x[i:i + WIN])
                if f.name.startswith("real_"):              # real speech is the hard negative: weight it like
                    for _ in range(REAL_NEG_EXTRA):         # the real positives (louder / quieter / faster copies)
                        X_neg.append(fit(augment(x[i:i + WIN], rng)))
    for _ in range(max(50, len(X_neg) // 10)):              # pure background / silence
        X_neg.append(np.clip(background(bg, rng, 0.02), -32768, 32767).astype(np.int16))
    return X_pos, X_neg


def embed(clips, F):
    if not clips:
        return np.zeros((0, 16 * 96), np.float32)
    e = F.embed_clips(np.stack(clips), batch_size=64)
    return e.reshape(len(clips), -1)


def stream_false_triggers(clf, audio: np.ndarray, threshold: float) -> int:
    """Run the real streaming detector over continuous audio and count triggers (2 s cooldown)."""
    from voice.wake import CustomWake
    w = CustomWake.__new__(CustomWake)
    from openwakeword.utils import AudioFeatures
    w.clf, w.frames, w.features, w.warm = clf, 16, AudioFeatures(inference_framework="onnx"), 0
    n, cooldown = 0, 0
    for i in range(0, len(audio) - 1280, 1280):
        s = w.predict(audio[i:i + 1280])
        cooldown -= 1
        if s >= threshold and cooldown <= 0:
            n, cooldown = n + 1, 25
    return n


def cmd_train(args):
    import joblib
    from openwakeword.utils import AudioFeatures
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rng = random.Random(1)
    pos = sorted(POS.glob("*.wav"))
    neg = sorted(NEG.glob("*.wav"))
    real_pos = [f for f in pos if f.name.startswith("real_")]
    real_neg = [f for f in neg if f.name.startswith("real_")]
    if len(pos) < 20 or len(neg) < 20:
        sys.exit("not enough data: run synth, record-pos and record-neg first")
    print(f"data: {len(real_pos)} real + {len(pos) - len(real_pos)} synthetic positives, "
          f"{len(real_neg)} real + {len(neg) - len(real_neg)} synthetic negative files")

    # hold out 20% of the REAL recordings to measure honestly
    rng.shuffle(real_pos)
    rng.shuffle(real_neg)
    hold_pos = real_pos[: max(1, len(real_pos) // 5)] if len(real_pos) >= 5 else []
    hold_neg = real_neg[: max(1, len(real_neg) // 5)] if len(real_neg) >= 5 else []
    train_pos = [f for f in pos if f not in hold_pos]
    train_neg = [f for f in neg if f not in hold_neg]
    bg_files = [f for f in train_neg if f.name.startswith("real_")]
    bg = np.concatenate([read_wav(f) for f in bg_files]) if bg_files else None

    Xp, Xn = build(train_pos, train_neg, bg, rng, args.aug)
    real_weight = 3   # real voice clips matter more than robot voices
    Xp += [place(augment(trim(read_wav(f)), rng), bg, rng) for f in train_pos if f.name.startswith("real_")
           for _ in range(args.aug * (real_weight - 1))]
    print(f"windows: {len(Xp)} positive, {len(Xn)} negative - embedding...")
    F = AudioFeatures(inference_framework="onnx")
    t = time.time()
    Ep, En = embed(Xp, F), embed(Xn, F)
    print(f"  embedded in {time.time() - t:.0f} s")
    X = np.vstack([Ep, En])
    y = np.r_[np.ones(len(Ep)), np.zeros(len(En))]
    clf = make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(64,), alpha=1e-3, max_iter=300,
                                                        early_stopping=True, random_state=0))
    t = time.time()
    clf.fit(X, y)
    print(f"  trained in {time.time() - t:.0f} s")

    report = {"date": time.strftime("%Y-%m-%d %H:%M"), "train_windows": {"pos": len(Ep), "neg": len(En)},
              "real_files": {"pos": len(real_pos), "neg": len(real_neg)}}
    thr = args.threshold
    if hold_pos:
        clips = [place(trim(read_wav(f)), None, rng) for f in hold_pos]
        scores = clf.predict_proba(embed(clips, F))[:, 1]
        report["holdout_recall"] = round(float((scores >= thr).mean()), 3)
        report["holdout_pos_files"] = len(hold_pos)
    if hold_neg:
        audio = np.concatenate([read_wav(f) for f in hold_neg])
        hours = len(audio) / SR / 3600
        n = stream_false_triggers(clf, audio, thr)
        report["false_triggers"] = n
        report["false_triggers_per_hour"] = round(n / hours, 1)
        report["holdout_neg_minutes"] = round(hours * 60, 1)
    report["threshold"] = thr
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"model": clf, "frames": 16, "threshold": thr, "report": report}, MODEL_PATH)
    (DATA / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"saved {MODEL_PATH}")
    if not hold_pos or not hold_neg:
        print("(record real clips with record-pos / record-neg for an honest held-out score)")


def cmd_live(args):
    from voice.wake import make_wake
    cfg = VoiceConfig()
    w = make_wake(args.model)
    print(f"live score for {args.model} - Ctrl+C to stop")
    with Mic(cfg.device, cfg.mic_rate, cfg.rate, cfg.chunk) as mic:
        try:
            while True:
                s = w.predict(mic.read())
                bar = "#" * int(s * 50)
                print(f"\r{s:5.2f} {bar:<50}{'  <-- WAKE' if s >= args.threshold else '          '}", end="", flush=True)
        except KeyboardInterrupt:
            print()


def main():
    p = argparse.ArgumentParser(description="Train the 'hey Beggy' wake word")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("record-pos"); a.add_argument("--n", type=int, default=40)
    a = sub.add_parser("record-neg"); a.add_argument("--seconds", type=float, default=180)
    a = sub.add_parser("synth"); a.add_argument("--n", type=int, default=300)
    a = sub.add_parser("train"); a.add_argument("--aug", type=int, default=4); a.add_argument("--threshold", type=float, default=0.5)
    a = sub.add_parser("live"); a.add_argument("--model", default=str(MODEL_PATH)); a.add_argument("--threshold", type=float, default=0.5)
    args = p.parse_args()
    {"record-pos": cmd_record_pos, "record-neg": cmd_record_neg, "synth": cmd_synth,
     "train": cmd_train, "live": cmd_live}[args.cmd](args)


if __name__ == "__main__":
    main()

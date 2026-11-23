#!/usr/bin/env python3
"""Posture evaluation: you act out each pose when told, the script compares what the detector
says with what you were asked to do.

    python eval_posture.py --source 8              # USB webcam, window with instructions
    python eval_posture.py --rounds 2 --hold 8     # 2 rounds, 8 s scored per pose

Needs calibration.json (run posture_main.py calibrate first, from the same camera position).
Each pose: 4 s to get into position (not scored), then `--hold` seconds scored frame by frame.
Writes eval_out/<date>/: report.md (tables for the slides), report.json, frames.csv,
correct/ and wrong/ example images.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

import cv2

from posture import Baseline, PostureConfig, compute_features
from posture.camera import open_source
from posture.classifier import find_issues
from posture.detector import PoseDetector, draw_debug
from posture.evaluate import markdown_report, predicted_class, score

POSES = [
    ("good", "SIT UP STRAIGHT, look at the camera"),
    ("slouch", "SLOUCH: hunch your back, head forward"),
    ("head_down", "LOOK DOWN at your phone / lap"),
    ("too_close", "LEAN IN close to the camera"),
    ("uneven_shoulders", "LEAN TO ONE SIDE (one shoulder lower)"),
    ("good", "SIT UP STRAIGHT again"),
    ("away", "LEAVE: step out of the camera view"),
]


def main() -> int:
    p = argparse.ArgumentParser(description="Posture detection evaluation")
    p.add_argument("--source", default="picam")
    p.add_argument("--rotate180", action="store_true")
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--hold", type=float, default=8.0, help="scored seconds per pose")
    p.add_argument("--prep", type=float, default=4.0, help="unscored seconds to get into each pose")
    p.add_argument("--no-window", action="store_true", help="instructions in the terminal only")
    p.add_argument("--skip-away", action="store_true", help="don't ask you to leave the view")
    args = p.parse_args()

    cfg = PostureConfig()
    try:
        base = Baseline.load(cfg.calibration_path)
    except FileNotFoundError:
        print("calibration.json not found - run `python posture_main.py calibrate` first", file=sys.stderr)
        return 1
    poses = [x for x in POSES if not (args.skip_away and x[0] == "away")]

    out = Path("eval_out") / time.strftime("%Y%m%d_%H%M%S")
    (out / "correct").mkdir(parents=True)
    (out / "wrong").mkdir()
    source = open_source(args.source, cfg.width, cfg.height)
    detector = PoseDetector(cfg.model_path)
    show = not args.no_window
    t0 = time.monotonic()
    rows, infer_ms = [], []
    saved = {}
    scored_time = 0.0

    def frame_and_detect():
        frame = source.read()
        if frame is None:
            raise RuntimeError("camera returned no frame")
        if args.rotate180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        ti = time.perf_counter()
        lms = detector.detect(frame, int((time.monotonic() - t0) * 1000))
        infer_ms.append((time.perf_counter() - ti) * 1000)
        return frame, lms

    def screen(frame, lms, lines, color):
        if not show:
            return True
        img = draw_debug(frame, lms, "", False)
        img = cv2.resize(img, None, fx=1.5, fy=1.5)
        cv2.rectangle(img, (0, 0), (img.shape[1], 40 + 40 * len(lines)), color, -1)
        for k, line in enumerate(lines):
            cv2.putText(img, line, (15, 38 + 40 * k), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow("posture evaluation", img)
        return (cv2.waitKey(1) & 0xFF) != ord("q")

    print(f"Evaluation: {args.rounds} round(s) x {len(poses)} poses, {args.hold:.0f} s each. Press q to stop.")
    try:
        for rnd in range(1, args.rounds + 1):
            for idx, (label, text) in enumerate(poses, 1):
                print(f"[round {rnd}] {idx}/{len(poses)}  {text}", flush=True)
                end = time.monotonic() + args.prep
                while time.monotonic() < end:
                    frame, lms = frame_and_detect()
                    left = end - time.monotonic()
                    if not screen(frame, lms, [f"NEXT: {text}", f"get ready... {left:.0f}"], (0, 140, 255)):
                        raise KeyboardInterrupt
                start = time.monotonic()
                end = start + args.hold
                while time.monotonic() < end:
                    frame, lms = frame_and_detect()
                    h, w = frame.shape[:2]
                    f = compute_features(lms, w, h, cfg.min_visibility) if lms is not None else None
                    issues = find_issues(f, base, cfg) if f else []
                    pred = predicted_class(f is not None, issues, label)
                    rows.append({"round": rnd, "truth": label, "pred": pred, "issues": "+".join(issues),
                                 "t": round(time.monotonic() - start, 2)})
                    ok = pred == label
                    key = (label, pred)
                    saved[key] = saved.get(key, 0) + 1
                    # keep a few examples per (asked, detected) pair, spread over the session
                    if saved[key] in (3, 25, 60) or (not ok and saved[key] in (1, 10)):
                        img = draw_debug(frame, lms, f"asked {label} / detected {pred}", not ok)
                        cv2.imwrite(str(out / ("correct" if ok else "wrong") / f"{label}_{pred}_r{rnd}_{saved[key]}.jpg"), img)
                    left = end - time.monotonic()
                    if not screen(frame, lms, [f"NOW: {text}", f"hold... {left:.0f}   detected: {pred}"],
                                  (60, 160, 60) if ok else (40, 40, 220)):
                        raise KeyboardInterrupt
                scored_time += args.hold
    except KeyboardInterrupt:
        print("stopped early - scoring what was recorded")
    finally:
        source.close()
        detector.close()
        if show:
            cv2.destroyAllWindows()

    if not rows:
        print("nothing recorded")
        return 1
    s = score(rows)
    perf = {
        "infer_ms_mean": round(statistics.mean(infer_ms), 1),
        "infer_ms_p95": round(sorted(infer_ms)[int(0.95 * (len(infer_ms) - 1))], 1),
        "model_fps": round(1000 / statistics.mean(infer_ms), 1),
        "pipeline_fps": round(len(rows) / scored_time, 1) if scored_time else None,
    }
    meta = {"date": time.strftime("%Y-%m-%d %H:%M"), "source": args.source, "rounds": args.rounds,
            "hold_s": args.hold, "baseline": base.__dict__, "thresholds": {
                "slouch_ratio": cfg.slouch_ratio, "head_down_delta": cfg.head_down_delta,
                "shoulder_tilt_deg": cfg.shoulder_tilt_deg, "too_close_ratio": cfg.too_close_ratio}}
    with open(out / "frames.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    (out / "report.json").write_text(json.dumps({"score": s, "perf": perf, "meta": meta}, indent=2))
    md = markdown_report(s, perf, meta)
    (out / "report.md").write_text(md)
    print(md)
    print(f"saved to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

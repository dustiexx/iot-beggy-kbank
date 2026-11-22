"""Beggy posture detection - entry point.

  python posture_main.py calibrate            # sit up straight for a few seconds
  python posture_main.py run                  # watch posture, print JSON events
  python posture_main.py run --source 0       # use a USB webcam (laptop testing)
  python posture_main.py run --debug-frame debug.jpg   # save the annotated frame (headless Pi)

Output is one JSON object per line on stdout:
  {"type": "posture_state", ...}   whenever the state changes (good/bad/away)
  {"type": "posture_status", ...}  every --status-every seconds
Later the control-board server will import PostureMonitor and push these to MQTT.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import cv2

from posture import Baseline, PostureClassifier, PostureConfig, compute_features
from posture.camera import open_source
from posture.detector import PoseDetector, draw_debug, draw_status


def emit(obj: dict) -> None:
    obj["unix_ms"] = int(time.time() * 1000)  # wall-clock time for the DB / dashboard
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def frames(source, detector: PoseDetector, cfg: PostureConfig, rotate180: bool):
    """Yield (frame, landmarks, features, now) at most cfg.fps_limit times per second."""
    t0 = time.monotonic()
    period = 1.0 / cfg.fps_limit
    next_t = 0.0
    frame_idx = 0
    while True:
        frame = source.read()
        if frame is None:
            return
        if source.realtime:
            now = time.monotonic()
        else:  # video file: use the video's own clock
            now = t0 + frame_idx / source.fps
            frame_idx += 1
        if now < next_t:
            continue
        next_t = now + period
        if rotate180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        h, w = frame.shape[:2]
        lms = detector.detect(frame, int((now - t0) * 1000))
        feats = compute_features(lms, w, h, cfg.min_visibility) if lms is not None else None
        yield frame, lms, feats, now


def cmd_calibrate(args, cfg: PostureConfig) -> int:
    source = open_source(args.source, cfg.width, cfg.height)
    detector = PoseDetector(cfg.model_path)
    print(f"Sit up straight and look at Beggy - capturing for {cfg.calibration_seconds:.0f}s...",
          file=sys.stderr)
    samples, start = [], None
    try:
        for _, _, feats, now in frames(source, detector, cfg, args.rotate180):
            start = start if start is not None else now
            if feats is not None:
                samples.append(feats)
            if now - start >= cfg.calibration_seconds:
                break
    finally:
        source.close()
        detector.close()

    if len(samples) < 5:
        print(f"Only {len(samples)} usable frames - make sure your head and both shoulders are in view.",
              file=sys.stderr)
        return 1
    base = Baseline.from_samples(samples)
    base.save(cfg.calibration_path)
    print(f"Saved baseline from {len(samples)} frames to {cfg.calibration_path}: {base.__dict__}",
          file=sys.stderr)
    return 0


def cmd_run(args, cfg: PostureConfig) -> int:
    try:
        baseline = Baseline.load(cfg.calibration_path)
    except FileNotFoundError:
        print(f"{cfg.calibration_path} not found - run `python posture_main.py calibrate` first.",
              file=sys.stderr)
        return 1

    clf = PostureClassifier(baseline, cfg)
    source = open_source(args.source, cfg.width, cfg.height)
    detector = PoseDetector(cfg.model_path)
    last_status = 0.0
    try:
        for frame, lms, feats, now in frames(source, detector, cfg, args.rotate180):
            st = clf.update(feats, now)
            if st.changed:
                emit({"type": "posture_state", **st.to_dict()})
            if now - last_status >= args.status_every:
                emit({"type": "posture_status", **st.to_dict()})
                last_status = now

            if args.show or args.debug_frame:
                img = draw_status(frame, lms, st, cfg.bad_after_s)
                if args.debug_frame:
                    cv2.imwrite(args.debug_frame, img)
                if args.show:
                    cv2.imshow("beggy posture", img)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    except KeyboardInterrupt:
        pass
    finally:
        source.close()
        detector.close()
        if args.show:
            cv2.destroyAllWindows()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Beggy posture detection")
    p.add_argument("command", choices=["calibrate", "run"])
    p.add_argument("--source", default="picam", help='"picam", a webcam index like "0", or a video file')
    p.add_argument("--model", default=None, help="path to pose_landmarker_*.task")
    p.add_argument("--calibration", default=None, help="baseline JSON path")
    p.add_argument("--rotate180", action="store_true", help="camera is mounted upside down")
    p.add_argument("--bad-after", type=float, default=None, help="seconds of bad posture before alert")
    p.add_argument("--status-every", type=float, default=5.0, help="seconds between status lines")
    p.add_argument("--show", action="store_true", help="open a preview window (needs a display)")
    p.add_argument("--debug-frame", default=None, help="keep writing the annotated frame to this jpg")
    args = p.parse_args()

    cfg = PostureConfig()
    if args.model:
        cfg.model_path = args.model
    if args.calibration:
        cfg.calibration_path = args.calibration
    if args.bad_after is not None:
        cfg.bad_after_s = args.bad_after

    try:
        return cmd_calibrate(args, cfg) if args.command == "calibrate" else cmd_run(args, cfg)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

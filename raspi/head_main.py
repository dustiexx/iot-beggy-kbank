#!/usr/bin/env python3
"""Beggy head: center the camera on the person, or move the head by hand.

    python head_main.py center                         # real servos (driver from head/config.py)
    python head_main.py center --driver sim --source 8 --show   # no servos: simulated head on a USB webcam
    python head_main.py home                           # back to the posture-calibration position
    python head_main.py move 120 90                    # pan, tilt in degrees (test wiring / directions)

Prints JSON lines: {"type": "head_step", ...} while moving, then {"type": "head_center", "status": ...}.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import cv2

from head.config import HeadConfig
from head.servo import make_head
from head.sim import SimHead
from head.tracker import center_on_person, target_point
from posture.camera import open_source
from posture.config import PostureConfig
from posture.detector import PoseDetector, draw_debug


def emit(obj: dict) -> None:
    obj["unix_ms"] = int(time.time() * 1000)
    print(json.dumps(obj), flush=True)


def cmd_center(args, cfg: HeadConfig, head) -> int:
    pcfg = PostureConfig()
    source = open_source(args.source, pcfg.width, pcfg.height)
    detector = PoseDetector(pcfg.model_path)
    t0 = time.monotonic()
    state = {"frame": None, "lms": None}

    def get_landmarks():
        frame = source.read()
        if frame is None:
            return None
        if args.rotate180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        if isinstance(head, SimHead):
            frame = head.view(frame).copy()
        lms = detector.detect(frame, int((time.monotonic() - t0) * 1000))
        state["frame"], state["lms"] = frame, lms
        if args.show:
            show(frame, lms)
        return lms

    def show(frame, lms):
        img = draw_debug(frame, lms, f"pan {head.pan:.0f} tilt {head.tilt:.0f}", False)
        h, w = img.shape[:2]
        cv2.drawMarker(img, (w // 2, int(h * cfg.target_y)), (0, 255, 0), cv2.MARKER_CROSS, 30, 2)
        pt = target_point(lms)
        if pt:
            cv2.circle(img, (int(pt[0] * w), int(pt[1] * h)), 8, (0, 0, 255), -1)
        cv2.imshow("beggy head", cv2.resize(img, None, fx=1.5, fy=1.5))
        cv2.waitKey(1)

    def on_step(info):
        emit({"type": "head_step", **{k: round(v, 3) if isinstance(v, float) else v for k, v in info.items()}})

    try:
        result = center_on_person(get_landmarks, head, cfg, on_step)
        emit({"type": "head_center", **result.to_dict()})
        if args.show:   # keep the window up a moment so you can see the end state
            end = time.monotonic() + 2
            while time.monotonic() < end:
                get_landmarks()
    finally:
        source.close()
        detector.close()
        if args.show:
            cv2.destroyAllWindows()
    return 0 if result.status == "centered" else 2


def main() -> int:
    p = argparse.ArgumentParser(description="Beggy pan/tilt head")
    p.add_argument("command", choices=["center", "home", "move"])
    p.add_argument("angles", nargs="*", type=float, help="for move: PAN TILT")
    p.add_argument("--driver", help="gpio | pca9685 | fake | sim (default from head/config.py)")
    p.add_argument("--source", default="picam", help='"picam" or a webcam index like "8"')
    p.add_argument("--rotate180", action="store_true")
    p.add_argument("--show", action="store_true", help="preview window")
    args = p.parse_args()

    cfg = HeadConfig()
    if args.driver:
        cfg.driver = args.driver
    head = make_head(cfg)
    try:
        if args.command == "home":
            emit({"type": "head_pos", "pan": head.pan, "tilt": head.tilt})
            return 0
        if args.command == "move":
            if len(args.angles) != 2:
                p.error("move needs PAN TILT")
            pan, tilt = head.move_to(*args.angles)
            emit({"type": "head_pos", "pan": pan, "tilt": tilt})
            time.sleep(0.5)   # give the servos time to get there before detaching
            return 0
        return cmd_center(args, cfg, head)
    finally:
        head.close()


if __name__ == "__main__":
    sys.exit(main())

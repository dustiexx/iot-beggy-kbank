#!/usr/bin/env python3
"""Beggy head: center the camera on the person, or move the head by hand.

    python head_main.py center                         # real servos (driver from head/config.py)
    python head_main.py center --driver sim --source 8 --show   # no servos: simulated head on a USB webcam
    python head_main.py home                           # back to the posture-calibration position
    python head_main.py move 120 90                    # pan, tilt in degrees (test wiring / directions)
    python head_main.py pan 100 --hold 3               # move ONE servo only (hwpwm), the other stays off;
    python head_main.py tilt 80 --raw                  #   --raw ignores the limits in head/config.py (to find them)
    python head_main.py spin 3 --dir 1                 # 360 pan: spin 3 s (+1/-1) to measure deg/s, no position kept
    python head_main.py pan-zero                       # 360 pan: "the head is at home_pan now" (after lining it up)

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
    p.add_argument("command", choices=["center", "home", "move", "pan", "tilt", "spin", "pan-zero"])
    p.add_argument("angles", nargs="*", type=float, help="for move: PAN TILT, for pan/tilt: ANGLE, for spin: SECONDS")
    p.add_argument("--driver", help="hwpwm | gpio | pca9685 | fake | sim (default from head/config.py)")
    p.add_argument("--source", default="picam", help='"picam" or a webcam index like "8"')
    p.add_argument("--rotate180", action="store_true")
    p.add_argument("--show", action="store_true", help="preview window")
    p.add_argument("--hold", type=float, default=0.5, help="seconds to hold the position before releasing")
    p.add_argument("--raw", action="store_true", help="pan/tilt: ignore the min/max limits (0..180)")
    p.add_argument("--dir", type=int, default=1, choices=[1, -1], help="spin: direction")
    args = p.parse_args()

    cfg = HeadConfig()
    if args.driver:
        cfg.driver = args.driver
    if args.command in ("spin", "pan-zero"):
        cfg.driver = args.driver or cfg.driver
        if cfg.driver != "hwpwm" or not cfg.pan_continuous:
            p.error(f"{args.command} is for the 360 deg pan servo (hwpwm driver, pan_continuous=True)")
        head = make_head(cfg, home=False)
        try:
            if args.command == "pan-zero":
                head.set_pan_estimate(cfg.home_pan)
                emit({"type": "head_pos", "pan": head.pan})
            else:
                if len(args.angles) != 1:
                    p.error("spin needs SECONDS")
                head.spin(args.dir, args.angles[0])
                emit({"type": "head_spin", "dir": args.dir, "seconds": args.angles[0],
                      "pulse_ms": cfg.pan_stop_ms + args.dir * cfg.pan_spin_ms})
        finally:
            head.close()
        return 0
    one_axis = args.command in ("pan", "tilt")
    if one_axis:
        if len(args.angles) != 1:
            p.error(f"{args.command} needs ANGLE")
        if cfg.driver != "hwpwm":
            p.error("pan/tilt need --driver hwpwm")
        if args.raw:
            cfg.pan_min = cfg.tilt_min = 0.0
            cfg.pan_max = cfg.tilt_max = 180.0
    head = make_head(cfg, home=not one_axis)
    try:
        if one_axis:
            angle = head.move_one(args.command, args.angles[0])
            emit({"type": "head_pos", args.command: angle})
            time.sleep(args.hold)
            return 0
        if args.command == "home":
            emit({"type": "head_pos", "pan": head.pan, "tilt": head.tilt})
            return 0
        if args.command == "move":
            if len(args.angles) != 2:
                p.error("move needs PAN TILT")
            pan, tilt = head.move_to(*args.angles)
            emit({"type": "head_pos", "pan": pan, "tilt": tilt})
            time.sleep(args.hold)   # give the servos time to get there before detaching
            return 0
        return cmd_center(args, cfg, head)
    finally:
        head.close()


if __name__ == "__main__":
    sys.exit(main())

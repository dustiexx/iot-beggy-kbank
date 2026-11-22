"""Thin wrapper around MediaPipe PoseLandmarker (Tasks API) running on the Pi."""
from __future__ import annotations

import os
from pathlib import Path

# MediaPipe/LiteRT log a lot to stderr on start-up; keep only errors
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

# landmarks we draw on the debug image (head + shoulders)
_DRAW = (0, 2, 5, 7, 8, 11, 12)


class PoseDetector:
    def __init__(self, model_path: str):
        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"{model_path} not found - run scripts/download_model.sh first")
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
        )
        self.landmarker = vision.PoseLandmarker.create_from_options(options)

    def detect(self, frame_bgr, timestamp_ms: int):
        """Returns the landmark list of the first person, or None."""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self.landmarker.detect_for_video(image, timestamp_ms)
        return result.pose_landmarks[0] if result.pose_landmarks else None

    def close(self):
        self.landmarker.close()


def draw_debug(frame_bgr, landmarks, status_text: str, bad: bool):
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    if landmarks is not None:
        pts = {i: (int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in _DRAW}
        cv2.line(out, pts[11], pts[12], (255, 200, 0), 2)
        cv2.line(out, pts[7], pts[8], (255, 200, 0), 2)
        for p in pts.values():
            cv2.circle(out, p, 4, (0, 255, 255), -1)
    color = (0, 0, 255) if bad else (0, 200, 0)
    cv2.putText(out, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return out


ISSUE_TEXT = {
    "slouch": "Slouching - sit up straight",
    "head_down": "Head down - look up",
    "uneven_shoulders": "Leaning to one side",
    "too_close": "Too close to the screen",
}

# BGR colours
_GREEN, _ORANGE, _RED, _GREY = (60, 180, 60), (0, 140, 255), (40, 40, 220), (120, 120, 120)


def draw_status(frame_bgr, landmarks, st, bad_after_s: float, scale: float = 1.5):
    """Bigger preview for --show: coloured banner with GOOD / WARNING / BAD / AWAY,
    a countdown before the alert fires, and the issues in plain words."""
    out = draw_debug(frame_bgr, landmarks, "", False)
    if scale != 1.0:
        out = cv2.resize(out, None, fx=scale, fy=scale)
    h, w = out.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX

    if st.state == "away":
        title, color, lines = "AWAY", _GREY, ["No one in front of the camera"]
    elif st.state == "bad":
        title, color = f"BAD POSTURE  {st.bad_seconds:.0f}s", _RED
        lines = [ISSUE_TEXT.get(i, i) for i in (st.raw_issues or st.issues)] or ["Fixing... hold it"]
    elif st.raw_issues:
        left = max(0.0, bad_after_s - st.bad_seconds)
        title, color = f"WARNING  alert in {left:.0f}s", _ORANGE
        lines = [ISSUE_TEXT.get(i, i) for i in st.raw_issues]
    else:
        title, color, lines = "GOOD POSTURE", _GREEN, []

    banner_h = 70 + 38 * len(lines)
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (w, banner_h), color, -1)
    out = cv2.addWeighted(overlay, 0.75, out, 0.25, 0)
    cv2.putText(out, title, (20, 50), font, 1.4, (255, 255, 255), 3, cv2.LINE_AA)
    for k, line in enumerate(lines):
        cv2.putText(out, "- " + line, (30, 95 + 38 * k), font, 0.9, (255, 255, 255), 2, cv2.LINE_AA)

    if st.features:
        f = st.features
        info = (f"neck {f['neck_ratio']:.2f}  head_drop {f['head_drop']:.3f}  "
                f"width {f['shoulder_width']:.0f}  tilt {f['shoulder_tilt_deg']:.1f}")
        cv2.putText(out, info, (10, h - 15), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(out, "q = quit", (w - 110, h - 15), font, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.rectangle(out, (0, 0), (w - 1, h - 1), color, 8)
    return out

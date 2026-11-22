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

"""Tunable settings for posture detection. Override from the CLI or by editing here."""
from dataclasses import dataclass


@dataclass
class PostureConfig:
    # --- camera ---
    width: int = 640
    height: int = 480
    fps_limit: float = 10.0          # analyse at most this many frames per second

    # --- pose model ---
    model_path: str = "models/pose_landmarker_lite.task"
    min_visibility: float = 0.5      # ignore landmarks the model is unsure about

    # --- thresholds (relative to the calibrated baseline) ---
    slouch_ratio: float = 0.85       # neck_ratio below baseline * this -> slouch / forward head
    head_down_delta: float = 0.07    # head_drop above baseline + this -> looking down
    shoulder_tilt_deg: float = 8.0   # |tilt - baseline| above this -> uneven shoulders
    too_close_ratio: float = 1.25    # shoulder width above baseline * this -> leaning in too close

    # --- timing ---
    ema_alpha: float = 0.3           # smoothing of features (0..1, higher = less smoothing)
    bad_after_s: float = 10.0        # bad posture must last this long before it counts
    good_after_s: float = 3.0        # good posture must last this long to clear the alert
    no_person_after_s: float = 3.0   # no body detected this long -> state "away"

    # --- calibration ---
    calibration_path: str = "calibration.json"
    calibration_seconds: float = 5.0

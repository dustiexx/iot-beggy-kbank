"""Turn MediaPipe pose landmarks into a few numbers that describe sitting posture.

The camera looks at the user from the front (on Beggy's head), so we only use
landmarks that are reliable from that angle: nose, eyes, ears and shoulders.
All lengths are divided by shoulder width, so the numbers don't change when the
user sits a bit nearer or farther.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

# MediaPipe Pose landmark indices
NOSE = 0
LEFT_EYE = 2
RIGHT_EYE = 5
LEFT_EAR = 7
RIGHT_EAR = 8
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12


@dataclass
class PostureFeatures:
    shoulder_width: float     # pixels; grows when the user leans toward the camera
    neck_ratio: float         # (shoulder line - ears) height / shoulder width; drops when slouching
    head_drop: float          # (nose - ears) height / shoulder width; grows when looking down
    shoulder_tilt_deg: float  # angle of the shoulder line, 0 = level
    head_roll_deg: float      # angle of the eye line, 0 = level

    def to_dict(self) -> dict:
        return {k: round(v, 4) for k, v in asdict(self).items()}


def _angle_deg(ax: float, ay: float, bx: float, by: float) -> float:
    """Angle of the line a->b against horizontal, folded into [-90, 90]."""
    deg = math.degrees(math.atan2(by - ay, bx - ax))
    if deg > 90:
        deg -= 180
    elif deg < -90:
        deg += 180
    return deg


def compute_features(landmarks, width: int, height: int, min_visibility: float = 0.5) -> PostureFeatures | None:
    """landmarks: list of objects with .x .y (normalised 0..1) and .visibility.

    Returns None when the shoulders or the head are not visible enough.
    """
    needed = [NOSE, LEFT_EAR, RIGHT_EAR, LEFT_SHOULDER, RIGHT_SHOULDER]
    if len(landmarks) <= RIGHT_SHOULDER:
        return None
    for i in needed:
        vis = getattr(landmarks[i], "visibility", 1.0)
        if vis is not None and vis < min_visibility:
            return None

    def px(i):
        return landmarks[i].x * width, landmarks[i].y * height

    nose = px(NOSE)
    l_eye, r_eye = px(LEFT_EYE), px(RIGHT_EYE)
    l_ear, r_ear = px(LEFT_EAR), px(RIGHT_EAR)
    l_sh, r_sh = px(LEFT_SHOULDER), px(RIGHT_SHOULDER)

    shoulder_width = math.dist(l_sh, r_sh)
    if shoulder_width < 1e-6:
        return None

    ear_y = (l_ear[1] + r_ear[1]) / 2
    shoulder_y = (l_sh[1] + r_sh[1]) / 2

    f = PostureFeatures(
        shoulder_width=shoulder_width,
        neck_ratio=(shoulder_y - ear_y) / shoulder_width,
        head_drop=(nose[1] - ear_y) / shoulder_width,
        shoulder_tilt_deg=_angle_deg(*r_sh, *l_sh),
        head_roll_deg=_angle_deg(*r_eye, *l_eye),
    )
    if implausible_reason(f, nose[0], l_sh[0], r_sh[0], width):
        return None
    return f


# Limits for "this is a real person sitting in front of the camera". MediaPipe sometimes fits a
# skeleton onto a chair, poster or clutter when nobody is there; those fits break these.
# Real sitting (measured on the USB webcam): width 290-620 px of 640, tilt within +-6 deg,
# neck_ratio 0.40-0.65 even when slouching hard.
MIN_SHOULDER_FRAC = 0.12     # shoulders narrower than 12% of the frame = too far / not a person
MAX_SHOULDER_TILT = 35.0
MAX_HEAD_ROLL = 45.0
MIN_NECK_RATIO = 0.05        # ears must be above the shoulders


def implausible_reason(f: PostureFeatures, nose_x: float, l_sh_x: float, r_sh_x: float, width: int) -> str | None:
    if f.shoulder_width < MIN_SHOULDER_FRAC * width:
        return "shoulders too small"
    if abs(f.shoulder_tilt_deg) > MAX_SHOULDER_TILT:
        return "shoulder tilt impossible"
    if abs(f.head_roll_deg) > MAX_HEAD_ROLL:
        return "head roll impossible"
    if f.neck_ratio < MIN_NECK_RATIO:
        return "ears not above shoulders"
    if not min(l_sh_x, r_sh_x) <= nose_x <= max(l_sh_x, r_sh_x):
        return "nose outside the shoulders"
    return None

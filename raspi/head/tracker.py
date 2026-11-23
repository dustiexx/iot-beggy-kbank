"""Turn the head so the person is in the middle of the camera frame."""
from __future__ import annotations

import time
from dataclasses import dataclass

from head.config import HeadConfig

# MediaPipe Pose landmark indices
NOSE, L_SHOULDER, R_SHOULDER = 0, 11, 12


def target_point(landmarks, min_visibility: float = 0.5) -> tuple[float, float] | None:
    """Point to center on, in normalized frame coords (0..1): between the nose and the
    middle of the shoulders, so head and shoulders both stay in view.
    Falls back to whatever of those is visible; None if nothing is."""
    if landmarks is None:
        return None

    def vis(i):
        lm = landmarks[i]
        return lm if getattr(lm, "visibility", 1.0) >= min_visibility else None

    nose = vis(NOSE)
    ls, rs = vis(L_SHOULDER), vis(R_SHOULDER)
    pts = []
    if nose:
        pts.append((nose.x, nose.y))
    if ls and rs:
        pts.append(((ls.x + rs.x) / 2, (ls.y + rs.y) / 2))
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def correction(point: tuple[float, float], cfg: HeadConfig) -> tuple[float, float, float, float]:
    """(err_x, err_y, d_pan, d_tilt) for one step. err is the offset from the target as a
    fraction of the frame (+x = person is right of center, +y = person is below target)."""
    err_x = point[0] - 0.5
    err_y = point[1] - cfg.target_y

    def step(err, fov, invert):
        d = err * fov * cfg.gain
        d = max(-cfg.max_step_deg, min(cfg.max_step_deg, d))
        return -d if invert else d

    # person on the right -> turn right -> lower pan angle; person low -> tilt down -> higher tilt angle
    d_pan = -step(err_x, cfg.hfov_deg, cfg.pan_invert) if abs(err_x) > cfg.deadband else 0.0
    d_tilt = step(err_y, cfg.vfov_deg, cfg.tilt_invert) if abs(err_y) > cfg.deadband else 0.0
    return err_x, err_y, d_pan, d_tilt


@dataclass
class CenterResult:
    status: str          # centered | no_person | timeout | at_limit
    pan: float
    tilt: float
    steps: int
    seconds: float

    def to_dict(self):
        return {"status": self.status, "pan": round(self.pan, 1), "tilt": round(self.tilt, 1),
                "steps": self.steps, "seconds": round(self.seconds, 2)}


def center_on_person(get_landmarks, head, cfg: HeadConfig, on_step=None) -> CenterResult:
    """Closed loop: look, move a bit toward the person, wait to settle, repeat.

    get_landmarks() -> landmarks of the person in the *current* view, or None.
    on_step(info_dict) is called after every step (for logging / preview)."""
    t0 = time.monotonic()
    last_seen = t0
    good = steps = 0
    while True:
        now = time.monotonic()
        if now - t0 > cfg.timeout_s:
            status = "timeout"
            break
        pt = target_point(get_landmarks())
        if pt is None:
            if now - last_seen > cfg.lost_after_s:
                status = "no_person"
                break
            continue
        last_seen = now
        err_x, err_y, d_pan, d_tilt = correction(pt, cfg)
        if d_pan == 0.0 and d_tilt == 0.0:
            good += 1
            if on_step:
                on_step({"err_x": err_x, "err_y": err_y, "pan": head.pan, "tilt": head.tilt, "moved": False})
            if good >= cfg.centered_frames:
                status = "centered"
                break
            continue
        good = 0
        before = (head.pan, head.tilt)
        head.move_to(head.pan + d_pan, head.tilt + d_tilt)
        steps += 1
        if on_step:
            on_step({"err_x": err_x, "err_y": err_y, "pan": head.pan, "tilt": head.tilt, "moved": True})
        if (head.pan, head.tilt) == before and head.at_limit:
            status = "at_limit"   # wants to go further but can't
            break
        time.sleep(cfg.settle_s)
    return CenterResult(status, head.pan, head.tilt, steps, time.monotonic() - t0)

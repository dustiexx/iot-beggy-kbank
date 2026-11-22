"""Decide good / bad posture from features, with a calibrated baseline and timing.

States:
  "good"  - posture OK
  "bad"   - at least one issue has lasted >= bad_after_s
  "away"  - no person seen for >= no_person_after_s
Small slips shorter than bad_after_s are ignored, so the bot doesn't nag.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .config import PostureConfig
from .metrics import PostureFeatures

ISSUES = ("slouch", "head_down", "uneven_shoulders", "too_close")


@dataclass
class Baseline:
    shoulder_width: float
    neck_ratio: float
    head_drop: float
    shoulder_tilt_deg: float

    @classmethod
    def from_samples(cls, samples: list[PostureFeatures]) -> "Baseline":
        if not samples:
            raise ValueError("no samples to calibrate from")
        med = lambda attr: statistics.median(getattr(s, attr) for s in samples)  # noqa: E731
        return cls(
            shoulder_width=med("shoulder_width"),
            neck_ratio=med("neck_ratio"),
            head_drop=med("head_drop"),
            shoulder_tilt_deg=med("shoulder_tilt_deg"),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Baseline":
        return cls(**json.loads(Path(path).read_text()))


def find_issues(f: PostureFeatures, base: Baseline, cfg: PostureConfig) -> list[str]:
    issues = []
    if f.neck_ratio < base.neck_ratio * cfg.slouch_ratio:
        issues.append("slouch")
    if f.head_drop > base.head_drop + cfg.head_down_delta:
        issues.append("head_down")
    if abs(f.shoulder_tilt_deg - base.shoulder_tilt_deg) > cfg.shoulder_tilt_deg:
        issues.append("uneven_shoulders")
    if f.shoulder_width > base.shoulder_width * cfg.too_close_ratio:
        issues.append("too_close")
    return issues


@dataclass
class PostureStatus:
    state: str                       # good / bad / away
    issues: list[str]                # issues that triggered "bad"
    raw_issues: list[str]            # issues seen in this frame (before timing)
    features: dict | None
    bad_seconds: float               # how long the current bad streak has lasted
    changed: bool                    # True on the update where the state changed
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "issues": self.issues,
            "raw_issues": self.raw_issues,
            "features": self.features,
            "bad_seconds": round(self.bad_seconds, 1),
            "timestamp": round(self.timestamp, 3),
        }


@dataclass
class PostureClassifier:
    baseline: Baseline
    cfg: PostureConfig = field(default_factory=PostureConfig)

    state: str = "away"
    _smoothed: PostureFeatures | None = None
    _bad_since: float | None = None
    _good_since: float | None = None
    _last_seen: float | None = None
    _active_issues: list[str] = field(default_factory=list)

    def _smooth(self, f: PostureFeatures) -> PostureFeatures:
        if self._smoothed is None:
            self._smoothed = f
            return f
        a = self.cfg.ema_alpha
        prev = self._smoothed
        self._smoothed = PostureFeatures(
            *(a * new + (1 - a) * old for new, old in zip(
                (f.shoulder_width, f.neck_ratio, f.head_drop, f.shoulder_tilt_deg, f.head_roll_deg),
                (prev.shoulder_width, prev.neck_ratio, prev.head_drop, prev.shoulder_tilt_deg, prev.head_roll_deg),
            ))
        )
        return self._smoothed

    def update(self, features: PostureFeatures | None, now: float) -> PostureStatus:
        old_state = self.state
        cfg = self.cfg

        if features is None:
            # keep the last decision briefly; switch to "away" after a while
            if self._last_seen is None or now - self._last_seen >= cfg.no_person_after_s:
                self.state = "away"
                self._smoothed = None
                self._bad_since = self._good_since = None
                self._active_issues = []
            return PostureStatus(self.state, list(self._active_issues), [], None,
                                 0.0, self.state != old_state, now)

        self._last_seen = now
        f = self._smooth(features)
        issues = find_issues(f, self.baseline, cfg)

        if issues:
            self._good_since = None
            if self._bad_since is None:
                self._bad_since = now
            if now - self._bad_since >= cfg.bad_after_s:
                self.state = "bad"
                self._active_issues = issues
            elif self.state == "away":
                self.state = "good"
        else:
            self._bad_since = None
            if self._good_since is None:
                self._good_since = now
            if self.state != "bad" or now - self._good_since >= cfg.good_after_s:
                self.state = "good"
                self._active_issues = []

        bad_seconds = now - self._bad_since if self._bad_since is not None else 0.0
        return PostureStatus(self.state, list(self._active_issues), issues, f.to_dict(),
                             bad_seconds, self.state != old_state, now)

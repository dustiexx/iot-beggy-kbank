"""Logic tests with fake landmarks - no camera or model needed.

Run from raspi/:  python -m pytest tests
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from posture import Baseline, PostureClassifier, PostureConfig, compute_features  # noqa: E402

W, H = 640, 480


def make_landmarks(ear_y=0.30, nose_y=0.32, shoulder_y=0.55, shoulder_half=0.18,
                   shoulder_tilt=0.0, vis=0.99):
    lm = [SimpleNamespace(x=0.5, y=0.5, visibility=vis) for _ in range(33)]
    lm[0] = SimpleNamespace(x=0.50, y=nose_y, visibility=vis)            # nose
    lm[2] = SimpleNamespace(x=0.53, y=ear_y - 0.03, visibility=vis)      # left eye
    lm[5] = SimpleNamespace(x=0.47, y=ear_y - 0.03, visibility=vis)      # right eye
    lm[7] = SimpleNamespace(x=0.58, y=ear_y, visibility=vis)             # left ear
    lm[8] = SimpleNamespace(x=0.42, y=ear_y, visibility=vis)             # right ear
    lm[11] = SimpleNamespace(x=0.5 + shoulder_half, y=shoulder_y - shoulder_tilt, visibility=vis)
    lm[12] = SimpleNamespace(x=0.5 - shoulder_half, y=shoulder_y + shoulder_tilt, visibility=vis)
    return lm


def feats(**kw):
    return compute_features(make_landmarks(**kw), W, H)


def make_clf(**cfg_kw):
    cfg = PostureConfig(**cfg_kw)
    base = Baseline.from_samples([feats() for _ in range(5)])
    return PostureClassifier(base, cfg)


def test_good_posture_features():
    f = feats()
    assert f.neck_ratio > 0.5
    assert abs(f.shoulder_tilt_deg) < 1
    assert abs(f.head_roll_deg) < 1


def test_hidden_shoulders_returns_none():
    assert compute_features(make_landmarks(vis=0.1), W, H) is None


def test_issue_detection():
    from posture.classifier import find_issues
    clf = make_clf()
    base, cfg = clf.baseline, clf.cfg
    assert find_issues(feats(), base, cfg) == []
    assert "slouch" in find_issues(feats(ear_y=0.40, nose_y=0.42), base, cfg)
    assert "head_down" in find_issues(feats(nose_y=0.38), base, cfg)
    assert "uneven_shoulders" in find_issues(feats(shoulder_tilt=0.04), base, cfg)
    assert "too_close" in find_issues(feats(shoulder_half=0.25), base, cfg)


def test_short_slouch_is_ignored_long_slouch_alerts():
    clf = make_clf(ema_alpha=1.0, bad_after_s=10, good_after_s=3)
    slouch = feats(ear_y=0.40, nose_y=0.42)
    assert clf.update(feats(), 0).state == "good"
    assert clf.update(slouch, 1).state == "good"
    assert clf.update(slouch, 5).state == "good"      # only 4 s so far
    st = clf.update(slouch, 11.5)
    assert st.state == "bad" and st.changed and "slouch" in st.issues
    assert clf.update(feats(), 12).state == "bad"     # needs 3 s of good to clear
    st = clf.update(feats(), 15.5)
    assert st.state == "good" and st.changed


def test_away_after_no_person():
    clf = make_clf(no_person_after_s=3)
    clf.update(feats(), 0)
    assert clf.update(None, 1).state == "good"
    st = clf.update(None, 4)
    assert st.state == "away" and st.changed


def test_baseline_roundtrip(tmp_path):
    b = Baseline.from_samples([feats(), feats(), feats()])
    p = tmp_path / "cal.json"
    b.save(p)
    assert Baseline.load(p) == b

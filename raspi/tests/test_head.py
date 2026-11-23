import pytest
from types import SimpleNamespace as P

import numpy as np

from head.config import HeadConfig
from head.servo import FakeHead
from head.sim import SimHead
from head.tracker import center_on_person, correction, target_point


def lms(nose=(0.5, 0.3), ls=(0.6, 0.6), rs=(0.4, 0.6), vis=1.0):
    pts = [P(x=0.5, y=0.5, visibility=0.0) for _ in range(33)]
    pts[0] = P(x=nose[0], y=nose[1], visibility=vis)
    pts[11] = P(x=ls[0], y=ls[1], visibility=vis)
    pts[12] = P(x=rs[0], y=rs[1], visibility=vis)
    return pts


def test_target_point_between_nose_and_shoulders():
    assert target_point(lms()) == pytest.approx((0.5, 0.45))
    assert target_point(lms(vis=0.1)) is None
    assert target_point(None) is None


def test_correction_directions_and_deadband():
    cfg = HeadConfig()
    # person on the right -> pan decreases (turn right)
    _, _, d_pan, d_tilt = correction((0.8, cfg.target_y), cfg)
    assert d_pan < 0 and d_tilt == 0
    # person low in frame -> tilt increases (look down)
    _, _, d_pan, d_tilt = correction((0.5, 0.8), cfg)
    assert d_pan == 0 and d_tilt > 0
    # inside the deadband -> no move
    assert correction((0.52, cfg.target_y + 0.02), cfg)[2:] == (0.0, 0.0)
    # inverted servo flips the direction
    _, _, d_pan_inv, _ = correction((0.8, cfg.target_y), HeadConfig(pan_invert=True))
    assert d_pan_inv > 0


def test_step_is_capped():
    cfg = HeadConfig(max_step_deg=5)
    assert abs(correction((1.0, 0.45), cfg)[2]) == 5


def test_head_clamps_to_limits():
    h = FakeHead(HeadConfig())
    assert h.move_to(500, -20) == (160, 60)
    assert h.at_limit


def test_center_converges_in_simulation():
    """Fake world: a person stands at a fixed angle; the camera sees them offset by
    (person angle - head angle) / fov. The loop must bring them to the target."""
    cfg = HeadConfig(settle_s=0, timeout_s=2)
    head = FakeHead(cfg)
    person_pan, person_tilt = 70.0, 100.0   # person is to the right and a bit low

    def get():
        x = 0.5 - (person_pan - head.pan) / cfg.hfov_deg
        y = cfg.target_y + (person_tilt - head.tilt) / cfg.vfov_deg
        return lms(nose=(x, y - 0.15), ls=(x + 0.1, y + 0.15), rs=(x - 0.1, y + 0.15))

    r = center_on_person(get, head, cfg)
    assert r.status == "centered"
    assert abs(head.pan - person_pan) < cfg.deadband * cfg.hfov_deg
    assert abs(head.tilt - person_tilt) < cfg.deadband * cfg.vfov_deg


def test_center_gives_up_without_person():
    cfg = HeadConfig(settle_s=0, lost_after_s=0.05, timeout_s=1)
    r = center_on_person(lambda: None, FakeHead(cfg), cfg)
    assert r.status == "no_person"


def test_sim_view_moves_opposite_to_head():
    cfg = HeadConfig()
    head = SimHead(cfg)
    frame = np.zeros((480, 640), dtype=np.uint8)
    frame[:, 320:] = 255                         # right half bright
    centre = head.view(frame).mean()
    head.move_to(cfg.home_pan - 15, cfg.home_tilt)  # turn right
    assert head.view(frame).mean() > centre        # sees more of the right side

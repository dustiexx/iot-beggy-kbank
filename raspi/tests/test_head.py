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


# --- hardware PWM driver against a fake /sys/class/pwm ---
from head.servo import HwPwmHead


@pytest.fixture
def hwpwm(tmp_path, monkeypatch):
    dev = tmp_path / "devices" / "1f00098000.pwm"
    dev.mkdir(parents=True)
    chip = tmp_path / "pwm" / "pwmchip0"
    chip.mkdir(parents=True)
    (chip / "device").symlink_to(dev)
    for ch in range(4):                       # pretend every channel is already exported
        d = chip / f"pwm{ch}"
        d.mkdir()
        for f in ("period", "duty_cycle", "enable"):
            (d / f).write_text("0")
    spins = []
    monkeypatch.setattr("head.servo.subprocess.run", lambda *a, **k: None)     # no real pinctrl
    monkeypatch.setattr("head.servo.time.sleep", lambda s: spins.append(s))

    def make(**kw):
        cfg = HeadConfig(driver="hwpwm", pan_state_path=str(tmp_path / "state.json"), **kw)
        return HwPwmHead(cfg, sysfs=str(tmp_path / "pwm")), chip, spins
    return make


def read(chip, ch, f):
    return int((chip / f"pwm{ch}" / f).read_text())


def test_hwpwm_positional_tilt_pulse_and_release(hwpwm):
    head, chip, _ = hwpwm(pan_continuous=False)
    head.move_one("tilt", 90)
    assert read(chip, 1, "period") == 20_000_000
    assert read(chip, 1, "duty_cycle") == 1_450_000         # 0.5 + 1.9 * 90/180 ms
    assert read(chip, 1, "enable") == 1
    assert read(chip, 0, "enable") == 0                     # pan untouched
    head.close()
    assert read(chip, 1, "enable") == 0


def test_hwpwm_slews_in_small_steps(hwpwm):
    head, chip, sleeps = hwpwm(pan_continuous=False, slew_deg_s=100)
    head.move_one("tilt", 90)
    sleeps.clear()
    head.move_one("tilt", 100)
    assert len(sleeps) == 10 and all(abs(s - 0.01) < 1e-9 for s in sleeps)


def test_hwpwm_continuous_pan_spins_for_delta_over_speed(hwpwm):
    head, chip, sleeps = hwpwm(pan_deg_s_pos=50.0, pan_deg_s_neg=40.0)
    assert head.pan == 90.0                                 # no state file yet -> home
    head.move_one("pan", 110)
    assert sleeps == [pytest.approx(20 / 50)]
    assert read(chip, 0, "duty_cycle") == 1_550_000         # stop 1.45 + spin 0.10
    assert read(chip, 0, "enable") == 0                     # stopped after the spin
    sleeps.clear()
    head.move_one("pan", 90)
    assert sleeps == [pytest.approx(20 / 40)]
    assert read(chip, 0, "duty_cycle") == 1_350_000


def test_hwpwm_continuous_pan_estimate_persists_and_clamps(hwpwm):
    head, _, sleeps = hwpwm(pan_max=120.0)
    assert head.move_one("pan", 170) == 120.0               # limits apply to the estimate
    head2, _, _ = hwpwm(pan_max=120.0)
    assert head2.pan == 120.0                               # read back from the state file
    sleeps.clear()
    head2.move_one("pan", 121)                              # clamped -> no move at all
    assert sleeps == []
    head2.set_pan_estimate(90.0)
    assert hwpwm()[0].pan == 90.0


def test_hwpwm_tiny_pan_moves_are_skipped(hwpwm):
    head, _, sleeps = hwpwm()
    head.move_to(91.0, 90.0)
    assert not any(s > 0.02 for s in sleeps)                # no pan spin for 1 deg

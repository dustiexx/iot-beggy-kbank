"""Pan/tilt drivers. They all share `Head`: move_to(pan, tilt) with clamping, home()."""
from __future__ import annotations

import sys

from head.config import HeadConfig


class Head:
    def __init__(self, cfg: HeadConfig):
        self.cfg = cfg
        self.pan = cfg.home_pan
        self.tilt = cfg.home_tilt

    def clamp(self, pan: float, tilt: float) -> tuple[float, float]:
        c = self.cfg
        return min(max(pan, c.pan_min), c.pan_max), min(max(tilt, c.tilt_min), c.tilt_max)

    def move_to(self, pan: float, tilt: float) -> tuple[float, float]:
        self.pan, self.tilt = self.clamp(pan, tilt)
        self._write(self.pan, self.tilt)
        return self.pan, self.tilt

    def home(self):
        return self.move_to(self.cfg.home_pan, self.cfg.home_tilt)

    @property
    def at_limit(self) -> bool:
        c = self.cfg
        return self.pan in (c.pan_min, c.pan_max) or self.tilt in (c.tilt_min, c.tilt_max)

    def _write(self, pan: float, tilt: float) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class FakeHead(Head):
    """No hardware: just logs the angles (stderr)."""

    def _write(self, pan, tilt):
        print(f"[fake head] pan={pan:.1f} tilt={tilt:.1f}", file=sys.stderr)


class GpioHead(Head):
    """Servos wired straight to the Pi's GPIO pins (signal), powered from a separate 5 V supply."""

    def __init__(self, cfg: HeadConfig):
        super().__init__(cfg)
        from gpiozero import AngularServo
        kw = dict(min_angle=0, max_angle=180,
                  min_pulse_width=cfg.min_pulse_ms / 1000, max_pulse_width=cfg.max_pulse_ms / 1000)
        self.pan_servo = AngularServo(cfg.pan_pin, **kw)
        self.tilt_servo = AngularServo(cfg.tilt_pin, **kw)

    def _write(self, pan, tilt):
        self.pan_servo.angle = pan
        self.tilt_servo.angle = tilt

    def close(self):
        self.pan_servo.detach()
        self.tilt_servo.detach()


class Pca9685Head(Head):
    """Servos on a PCA9685 board (I2C address 0x40). Needs: pip install adafruit-circuitpython-servokit"""

    def __init__(self, cfg: HeadConfig):
        super().__init__(cfg)
        from adafruit_servokit import ServoKit
        self.kit = ServoKit(channels=16)
        for ch in (cfg.pan_channel, cfg.tilt_channel):
            self.kit.servo[ch].set_pulse_width_range(int(cfg.min_pulse_ms * 1000), int(cfg.max_pulse_ms * 1000))

    def _write(self, pan, tilt):
        self.kit.servo[self.cfg.pan_channel].angle = pan
        self.kit.servo[self.cfg.tilt_channel].angle = tilt


def make_head(cfg: HeadConfig) -> Head:
    from head.sim import SimHead
    drivers = {"fake": FakeHead, "gpio": GpioHead, "pca9685": Pca9685Head, "sim": SimHead}
    if cfg.driver not in drivers:
        raise ValueError(f"unknown head driver {cfg.driver!r} (choose from {', '.join(drivers)})")
    head = drivers[cfg.driver](cfg)
    head.home()
    return head

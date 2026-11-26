"""Pan/tilt drivers. They all share `Head`: move_to(pan, tilt) with clamping, home()."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

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


class HwPwmHead(Head):
    """Servos on the Pi 5's hardware PWM (RP1 PWM0) through /sys/class/pwm: steady pulses, no jitter.

    Needs in /boot/firmware/config.txt:  dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
    Moves are slewed (cfg.slew_deg_s) so the servos never jump, which keeps the current spikes small.
    With cfg.pan_continuous (a 360 deg servo on pan) pan moves are timed spins from an estimated angle.
    """
    PERIOD_NS = 20_000_000                      # 50 Hz
    CHANNEL = {12: 0, 13: 1, 18: 2, 19: 3}      # GPIO -> RP1 PWM0 channel
    STEP_DEG = 1.0

    def __init__(self, cfg: HeadConfig, sysfs: str = "/sys/class/pwm"):
        self._last: dict[int, float] = {}
        self.chip = self._find_chip(Path(sysfs))
        self.pins = {"pan": cfg.pan_pin, "tilt": cfg.tilt_pin}
        for pin in self.pins.values():
            if pin not in self.CHANNEL:
                raise ValueError(f"GPIO{pin} has no hardware PWM (use 12, 13, 18 or 19)")
            self._setup(pin)
        super().__init__(cfg)
        if cfg.pan_continuous:                  # position is an estimate, kept across processes
            self.pan = self._load_pan_estimate()

    @staticmethod
    def _find_chip(root: Path) -> Path:
        for chip in sorted(root.glob("pwmchip*")):
            if "1f00098000.pwm" in str((chip / "device").resolve()):   # RP1 PWM0 (pwmchip1 is the fan)
                return chip
        raise RuntimeError("RP1 PWM0 not found - add dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4 "
                           "to /boot/firmware/config.txt and reboot")

    def _ch(self, pin: int) -> Path:
        return self.chip / f"pwm{self.CHANNEL[pin]}"

    def _put(self, path: Path, value) -> None:
        for _ in range(50):                    # udev gives the gpio group access a moment after export
            try:
                path.write_text(str(value))
                return
            except PermissionError:
                time.sleep(0.02)
        path.write_text(str(value))

    def _setup(self, pin: int) -> None:
        if not self._ch(pin).exists():
            self._put(self.chip / "export", self.CHANNEL[pin])
        subprocess.run(["pinctrl", "set", str(pin), "a0"], check=False,   # in case gpiozero took the pin over
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._put(self._ch(pin) / "period", self.PERIOD_NS)

    def pulse_ns(self, angle: float) -> int:
        c = self.cfg
        ms = c.min_pulse_ms + (c.max_pulse_ms - c.min_pulse_ms) * min(max(angle, 0.0), 180.0) / 180.0
        return round(ms * 1_000_000)

    def _set(self, pin: int, angle: float) -> None:
        self._put(self._ch(pin) / "duty_cycle", self.pulse_ns(angle))
        if pin not in self._last:
            self._put(self._ch(pin) / "enable", 1)
        self._last[pin] = angle

    def _slew(self, pin: int, target: float) -> None:
        cur = self._last.get(pin)
        if cur is None:                         # first move: position unknown, go straight there
            self._set(pin, target)
            return
        n = max(1, int(abs(target - cur) / self.STEP_DEG))
        for i in range(1, n + 1):
            self._set(pin, cur + (target - cur) * i / n)
            time.sleep(self.STEP_DEG / self.cfg.slew_deg_s)

    # --- 360 deg continuous-rotation pan: the pulse sets speed, so moves are timed spins ---
    def _load_pan_estimate(self) -> float:
        try:
            return float(json.loads(Path(self.cfg.pan_state_path).read_text())["pan"])
        except (OSError, ValueError, KeyError):
            return self.cfg.home_pan

    def _save_pan_estimate(self, pan: float) -> None:
        try:
            Path(self.cfg.pan_state_path).write_text(json.dumps({"pan": round(pan, 2)}))
        except OSError:
            pass

    def spin(self, direction: int, seconds: float) -> None:
        """Turn the continuous pan servo for `seconds` (+1 = toward higher pan angles), then stop."""
        c = self.cfg
        if direction == 0 or seconds <= 0:
            return
        pin = self.pins["pan"]
        ms = c.pan_stop_ms + direction * c.pan_spin_ms   # pan_invert is handled by the tracker
        self._put(self._ch(pin) / "duty_cycle", round(ms * 1_000_000))
        self._put(self._ch(pin) / "enable", 1)
        try:
            time.sleep(seconds)
        finally:
            self._put(self._ch(pin) / "enable", 0)   # no pulses = the continuous servo stops

    def _pan_timed(self, old: float, target: float) -> None:
        c = self.cfg
        delta = target - old
        if abs(delta) < c.pan_min_move_deg:
            return
        rate = c.pan_deg_s_pos if delta > 0 else c.pan_deg_s_neg
        self.spin(1 if delta > 0 else -1, abs(delta) / rate)
        self._save_pan_estimate(target)

    def move_to(self, pan: float, tilt: float) -> tuple[float, float]:
        if not self.cfg.pan_continuous:
            return super().move_to(pan, tilt)
        old = self.pan
        self.pan, self.tilt = self.clamp(pan, tilt)
        self._pan_timed(old, self.pan)          # one servo at a time: smaller current peaks
        self._slew(self.pins["tilt"], self.tilt)
        return self.pan, self.tilt

    def set_pan_estimate(self, pan: float) -> None:
        """Tell the driver where pan really is (e.g. after lining the head up by eye)."""
        self.pan = pan
        self._save_pan_estimate(pan)

    def _write(self, pan, tilt):
        self._slew(self.pins["pan"], pan)       # one servo at a time: smaller current peaks
        self._slew(self.pins["tilt"], tilt)

    def move_one(self, axis: str, angle: float) -> float:
        """Move only one servo (pan or tilt); the other is not touched / not powered up."""
        c = self.cfg
        lo, hi = (c.pan_min, c.pan_max) if axis == "pan" else (c.tilt_min, c.tilt_max)
        angle = min(max(angle, lo), hi)
        if axis == "pan" and c.pan_continuous:
            self._pan_timed(self.pan, angle)
        else:
            self._slew(self.pins[axis], angle)
        setattr(self, axis, angle)
        return angle

    def release(self) -> None:
        """Stop the pulses: the servos go limp and quiet (no holding current)."""
        for pin in list(self._last):
            self._put(self._ch(pin) / "enable", 0)
        self._last.clear()

    def close(self):
        self.release()


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


def make_head(cfg: HeadConfig, home: bool = True) -> Head:
    from head.sim import SimHead
    drivers = {"fake": FakeHead, "gpio": GpioHead, "hwpwm": HwPwmHead, "pca9685": Pca9685Head, "sim": SimHead}
    if cfg.driver not in drivers:
        raise ValueError(f"unknown head driver {cfg.driver!r} (choose from {', '.join(drivers)})")
    head = drivers[cfg.driver](cfg)
    if home:
        head.home()
    return head

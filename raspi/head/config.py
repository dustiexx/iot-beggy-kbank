"""Pan/tilt head settings. Angles are servo degrees (0..180); 90 is straight ahead."""
from dataclasses import dataclass


@dataclass
class HeadConfig:
    # --- hardware ---
    driver: str = "gpio"             # gpio | pca9685 | fake | sim
    pan_pin: int = 12                # GPIO (BCM) for the gpio driver; Pi 5 hardware-PWM capable pins: 12, 13, 18, 19
    tilt_pin: int = 13
    pan_channel: int = 0             # PCA9685 channels for the pca9685 driver
    tilt_channel: int = 1
    min_pulse_ms: float = 0.5        # check your servo's datasheet (SG90/MG90S: ~0.5-2.4 ms)
    max_pulse_ms: float = 2.4

    # --- limits & home (home = where posture calibration is done) ---
    pan_min: float = 20.0
    pan_max: float = 160.0
    tilt_min: float = 60.0
    tilt_max: float = 130.0
    home_pan: float = 90.0
    home_tilt: float = 90.0
    pan_invert: bool = False         # flip if the head turns the wrong way
    tilt_invert: bool = False

    # --- camera field of view (Camera Module 3 standard lens: 66 x 41 deg) ---
    hfov_deg: float = 66.0
    vfov_deg: float = 41.0

    # --- centering loop ---
    gain: float = 0.6                # fraction of the measured error corrected per step (0..1)
    max_step_deg: float = 10.0       # biggest single move, keeps the motion smooth
    deadband: float = 0.06           # |error| below this (fraction of frame) counts as centered
    target_y: float = 0.45           # where the face/shoulder point should sit vertically (0 top, 1 bottom)
    settle_s: float = 0.25           # wait after each move so the frame isn't blurred
    centered_frames: int = 3         # consecutive centered frames needed to finish
    timeout_s: float = 8.0
    lost_after_s: float = 2.0        # give up if nobody is visible this long

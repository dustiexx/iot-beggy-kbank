"""Control-board settings.

Two kinds:
- Env: fixed at start-up, from raspi/.env (broker, certificates, camera, drivers).
- Runtime: can be changed while running from the dashboard (MQTT beggy/pi/settings or POST /settings);
  saved to settings.json so they survive a restart.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name, default, cast=str):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.lower() in ("1", "true", "yes", "on")
    return cast(v)


@dataclass
class Env:
    # MQTT: local Mosquitto by default; AWS IoT Core = host <prefix>-ats.iot.<region>.amazonaws.com, port 8883 + certs
    mqtt_host: str
    mqtt_port: int
    mqtt_client_id: str
    mqtt_ca: str | None
    mqtt_cert: str | None
    mqtt_key: str | None
    mqtt_retain: bool           # False if the AWS IoT policy lacks iot:RetainPublish (it drops the connection)
    topic_prefix: str
    # hardware
    camera_source: str          # picam | webcam index | video file
    camera_rotate180: bool
    head_driver: str            # hwpwm | gpio | pca9685 | fake | sim
    mic_device: str
    audio_gain_db: float        # louder TTS playback (soft-limited), e.g. 8
    whisper_model: str          # offline STT backup (faster-whisper): base | off | path to a CT2 model
    audio_out: str              # none = save replies to tts_out/ instead of playing | default | ALSA device e.g. hw:3,0
    wake_mode: str              # wake | off
    ups_enabled: bool
    # API
    api_port: int

    @classmethod
    def load(cls) -> "Env":
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        return cls(
            mqtt_host=_env("MQTT_HOST", "localhost"),
            mqtt_port=_env("MQTT_PORT", 1883, int),
            mqtt_client_id=_env("MQTT_CLIENT_ID", "beggy-pi"),
            mqtt_ca=_env("MQTT_CA", None),
            mqtt_cert=_env("MQTT_CERT", None),
            mqtt_key=_env("MQTT_KEY", None),
            mqtt_retain=_env("MQTT_RETAIN", True, bool),
            topic_prefix=_env("TOPIC_PREFIX", "beggy"),
            camera_source=_env("CAMERA_SOURCE", "picam"),
            camera_rotate180=_env("CAMERA_ROTATE180", False, bool),
            head_driver=_env("HEAD_DRIVER", "fake"),
            mic_device=_env("MIC_DEVICE", "DGM20"),
            audio_out=_env("AUDIO_OUT", "none"),
            audio_gain_db=_env("AUDIO_GAIN_DB", 0.0, float),
            whisper_model=_env("WHISPER_MODEL", "base"),
            wake_mode=_env("WAKE_MODE", "wake"),
            ups_enabled=_env("UPS_ENABLED", True, bool),
            api_port=_env("API_PORT", 8080, int),
        )


@dataclass
class Runtime:
    posture_enabled: bool = True
    posture_bad_after_s: float = 10.0
    posture_status_every_s: float = 5.0
    head_auto_home_s: float = 30.0       # after centering, go back home (posture position) after this long
    voice_enabled: bool = True
    wakeword: str = "hey_jarvis"         # or models/hey_beggy.joblib once trained on real recordings
    wake_threshold: float = 0.5
    tts_voice: str = "th-TH-PremwadeeNeural"
    battery_every_s: float = 30.0
    battery_low_pct: float = 20          # iPhone-style: warn once at 20 %...
    battery_very_low_pct: float = 10     # ...and once more at 10 % (no automatic shutdown)


class Settings:
    """Thread-safe runtime settings with change listeners."""

    def __init__(self, path: Path = ROOT / "settings.json"):
        self.path = path
        self._lock = threading.Lock()
        self._listeners = []
        self.rt = Runtime()
        if path.exists():
            self._apply(json.loads(path.read_text()), save=False)

    def get(self) -> dict:
        with self._lock:
            return asdict(self.rt)

    def on_change(self, fn):
        self._listeners.append(fn)

    def update(self, changes: dict) -> dict:
        """Apply known keys (type-converted); returns {"applied": {...}, "ignored": [...]}."""
        result = self._apply(changes, save=True)
        for fn in self._listeners:
            fn(result["applied"])
        return result

    def _apply(self, changes: dict, save: bool) -> dict:
        types = {f.name: f.type for f in fields(Runtime)}
        applied, ignored = {}, []
        with self._lock:
            for k, v in changes.items():
                if k not in types:
                    ignored.append(k)
                    continue
                cast = {"bool": lambda x: x if isinstance(x, bool) else str(x).lower() in ("1", "true", "yes", "on"),
                        "float": float, "str": str, "int": int}[types[k]]
                try:
                    setattr(self.rt, k, cast(v))
                    applied[k] = getattr(self.rt, k)
                except (TypeError, ValueError):
                    ignored.append(k)
            if save and applied:
                self.path.write_text(json.dumps(asdict(self.rt), indent=2))
        return {"applied": applied, "ignored": ignored}

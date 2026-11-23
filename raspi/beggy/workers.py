"""Background workers of the control board. Each runs in its own thread and reports through `bus`
(a callable: bus(topic_name, payload_dict) -> publishes to MQTT and keeps the latest value)."""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time

import numpy as np

log = logging.getLogger("workers")


# ---------------------------------------------------------------- vision: posture + head
class VisionWorker(threading.Thread):
    """Owns the camera. Normally runs posture detection; on a "center" command it runs the
    head-centering loop with the same camera, then pauses posture until the head is home again
    (posture is only valid from the calibrated home position)."""

    def __init__(self, env, settings, bus):
        super().__init__(name="vision", daemon=True)
        self.env, self.settings, self.bus = env, settings, bus
        self.cmds: queue.Queue = queue.Queue()
        self.fps = 0.0
        self.stop_evt = threading.Event()

    def request(self, action: str, timeout: float = 15) -> dict:
        """Run a head action on the vision thread and wait for its result."""
        done = queue.Queue(maxsize=1)
        self.cmds.put((action, done))
        try:
            return done.get(timeout=timeout)
        except queue.Empty:
            return {"status": "timeout"}

    def run(self):
        import cv2

        from head.config import HeadConfig
        from head.servo import make_head
        from head.sim import SimHead
        from head.tracker import center_on_person
        from posture import Baseline, PostureClassifier, PostureConfig, compute_features
        from posture.camera import open_source
        from posture.detector import PoseDetector

        pcfg = PostureConfig()
        hcfg = HeadConfig(driver=self.env.head_driver)
        head = make_head(hcfg)
        source = open_source(self.env.camera_source, pcfg.width, pcfg.height)
        detector = PoseDetector(pcfg.model_path)
        try:
            baseline = Baseline.load(pcfg.calibration_path)
        except FileNotFoundError:
            baseline = None
            log.warning("no calibration.json - posture disabled (run posture_main.py calibrate)")

        def new_classifier():
            rt = self.settings.get()
            pcfg.bad_after_s = rt["posture_bad_after_s"]
            return PostureClassifier(baseline, pcfg) if baseline else None

        clf = new_classifier()
        self.settings.on_change(lambda ch: ch.keys() & {"posture_bad_after_s"} and self.cmds.put(("reload", None)))
        t0 = time.monotonic()
        last_status = 0.0
        frame_times = collections.deque(maxlen=30)
        home_at = None   # when the head should return home after centering

        def grab():
            frame = source.read()
            if frame is None:
                return None, None
            if self.env.camera_rotate180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            if isinstance(head, SimHead):
                frame = head.view(frame).copy()
            lms = detector.detect(frame, int((time.monotonic() - t0) * 1000))
            frame_times.append(time.monotonic())
            if len(frame_times) > 1:
                self.fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
            return frame, lms

        def go_home():
            nonlocal home_at, clf
            head.home()
            home_at = None
            time.sleep(hcfg.settle_s)
            clf = new_classifier()   # fresh smoothing, the view just changed
            self.bus("head", {"type": "head_pos", "pan": head.pan, "tilt": head.tilt, "home": True})

        try:
            while not self.stop_evt.is_set():
                # commands
                try:
                    action, done = self.cmds.get_nowait()
                except queue.Empty:
                    action = None
                if action == "center":
                    res = center_on_person(lambda: grab()[1], head, hcfg).to_dict()
                    home_at = time.monotonic() + self.settings.get()["head_auto_home_s"]
                    self.bus("head", {"type": "head_center", **res})
                    self.bus("posture", {"type": "posture_state", "state": "paused", "reason": "head moved"})
                    done.put(res)
                    continue
                if action == "home":
                    go_home()
                    done.put({"status": "home", "pan": head.pan, "tilt": head.tilt})
                    continue
                if action == "reload":
                    clf = new_classifier()
                if home_at and time.monotonic() >= home_at:
                    go_home()

                frame, lms = grab()
                if frame is None:
                    log.error("camera returned no frame")
                    time.sleep(1)
                    continue
                rt = self.settings.get()
                if home_at or not clf or not rt["posture_enabled"]:
                    continue
                h, w = frame.shape[:2]
                feats = compute_features(lms, w, h, pcfg.min_visibility) if lms is not None else None
                st = clf.update(feats, time.monotonic())
                now = time.monotonic()
                if st.changed:
                    self.bus("posture", {"type": "posture_state", **st.to_dict()})
                if now - last_status >= rt["posture_status_every_s"]:
                    self.bus("posture", {"type": "posture_status", **st.to_dict(), "fps": round(self.fps, 1)})
                    last_status = now
                # limit to ~10 fps: enough for posture, leaves CPU for the rest
                time.sleep(max(0.0, 1 / pcfg.fps_limit - (time.monotonic() - now)))
        finally:
            source.close()
            detector.close()
            head.close()


# ---------------------------------------------------------------- voice: wake -> STT -> Gemini -> TTS
class VoiceWorker(threading.Thread):
    def __init__(self, env, settings, bus, robot, speaker):
        super().__init__(name="voice", daemon=True)
        self.env, self.settings, self.bus, self.robot, self.speaker = env, settings, bus, robot, speaker
        self.stop_evt = threading.Event()
        self.busy = threading.Lock()   # one conversation at a time (voice or POST /ask)
        self.stt = self.assistant = None

    def _ensure_ai(self):
        if self.assistant is None:
            from beggy.assistant import Assistant, Stt
            self.stt, self.assistant = Stt(), Assistant(self.robot)

    def handle_text(self, text: str, source: str = "voice", speak: bool = True) -> dict:
        """Text -> Gemini -> reply (spoken). Also used by the API / MQTT 'ask' command."""
        with self.busy:
            self._ensure_ai()
            t0 = time.time()
            r = self.assistant.ask(text)
            out = {"type": "voice_reply", "source": source, "transcript": text, **r,
                   "think_s": round(time.time() - t0, 2)}
            if speak and r["reply"]:
                out["tts"] = self.speaker.say(r["reply"], self.settings.get()["tts_voice"])
            self.bus("voice", out)
            return out

    def run(self):
        from voice.audio import Mic, rms, to_wav
        from voice.config import VoiceConfig
        from voice_main import record_utterance

        vcfg = VoiceConfig(device=self.env.mic_device)
        wake = None
        if self.env.wake_mode == "wake":
            from voice.wake import make_wake
            wake = make_wake(self.settings.get()["wakeword"])   # built-in name or models/hey_beggy.joblib
        else:
            log.info("WAKE_MODE=%s: voice only through POST /ask or the 'ask' command", self.env.wake_mode)
            self.reported_dead = True   # not an error: there is just no mic loop
            return
        levels = collections.deque(maxlen=int(3 * vcfg.rate / vcfg.chunk))
        with Mic(vcfg.device, vcfg.mic_rate, vcfg.rate, vcfg.chunk) as mic:
            log.info("listening for wake word '%s'", self.settings.get()["wakeword"])
            while not self.stop_evt.is_set():
                c = mic.read()
                levels.append(rms(c))
                rt = self.settings.get()
                if not rt["voice_enabled"]:
                    continue
                score = wake.predict(c)
                if score < rt["wake_threshold"]:
                    continue
                self.bus("voice", {"type": "wake", "score": round(float(score), 3)})
                audio = record_utterance(mic, vcfg, float(np.median(levels)) if levels else 100.0)
                try:
                    if audio is None:
                        self.bus("voice", {"type": "no_speech"})
                    else:
                        self._ensure_ai()
                        t0 = time.time()
                        text = self.stt.transcribe(to_wav(audio, vcfg.rate))
                        stt_s = round(time.time() - t0, 2)
                        if not text:
                            self.bus("voice", {"type": "no_speech", "seconds": round(len(audio) / vcfg.rate, 2)})
                        else:
                            self.bus("voice", {"type": "transcript", "text": text, "stt_s": stt_s})
                            self.handle_text(text)
                except Exception as e:   # network / quota problems must not kill the robot
                    log.exception("voice pipeline failed")
                    self.bus("voice", {"type": "voice_error", "error": str(e)[:300]})
                mic.flush()
                wake.reset()
                end = time.time() + vcfg.wake_cooldown_s
                while time.time() < end:
                    mic.read()


# ---------------------------------------------------------------- battery (UPS HAT E, I2C)
class BatteryAlerts:
    """iPhone-style low battery warnings: once at 20 %, once more at 10 %, re-armed when charging.

    Percent comes from the HAT's fuel gauge, which can be off for the first charge cycles, so a
    low cell voltage also counts: the weakest cell at <= cell_v_10 is treated as 10 %."""

    def __init__(self):
        self.warned: set[int] = set()

    def update(self, r: dict, levels=(20, 10), cell_v_10: float = 3.45) -> int | None:
        """Returns the level (20 / 10) to warn about now, or None."""
        if r["plugged_in"] or r["charging"]:
            self.warned.clear()
            return None
        pct = r["percent"]
        if r["min_cell_v"] <= cell_v_10:
            pct = min(pct, min(levels))
        due = [lv for lv in sorted(levels) if pct <= lv and lv not in self.warned]
        if not due:
            return None
        self.warned.update(lv for lv in levels if lv >= due[0])   # 20 % is skipped if we're already at 10 %
        return due[0]


class BatteryWorker(threading.Thread):
    def __init__(self, settings, bus, say=None):
        super().__init__(name="battery", daemon=True)
        self.settings, self.bus, self.say = settings, bus, say
        self.stop_evt = threading.Event()
        self.alerts = BatteryAlerts()

    def run(self):
        from beggy.ups import Ups
        try:
            ups = Ups()
        except Exception as e:
            log.warning("UPS HAT not available: %s", e)
            return
        while not self.stop_evt.is_set():
            rt = self.settings.get()
            try:
                r = ups.read()
            except OSError as e:
                log.warning("UPS read failed: %s", e)
                self.stop_evt.wait(rt["battery_every_s"])
                continue
            self.bus("battery", {"type": "battery_status", **r})
            level = self.alerts.update(r, (int(rt["battery_low_pct"]), int(rt["battery_very_low_pct"])))
            if level:
                self.bus("battery", {"type": "battery_low", "level_pct": level, "percent": r["percent"],
                                     "min_cell_v": r["min_cell_v"], "minutes_to_empty": r["minutes_to_empty"]})
                if self.say:
                    try:
                        self.say(f"แบตเตอรี่เหลือ {r['percent']} เปอร์เซ็นต์ค่ะ ช่วยเสียบสายชาร์จให้เบกกี้ด้วยนะคะ")
                    except Exception:
                        log.exception("could not speak the battery warning")
            self.stop_evt.wait(rt["battery_every_s"])


# ---------------------------------------------------------------- calendar feed for deadline notifications
class CalendarWorker(threading.Thread):
    """Every few minutes publish on beggy/pi/calendar either
      {"type": "calendar_upcoming", "hours": 24, "events": [...]}   the next 24 h, or
      {"type": "calendar_error", "reason": "not_connected" | "login_expired" | "api_error", "error": "..."}
    (calendar_created / calendar_deleted are sent right away by Robot.calendar when Gemini changes events)
    so the backend can send "calendar deadline" notifications and the dashboard can tell
    "no events" apart from "Beggy can't read the calendar"."""

    def __init__(self, robot, bus, every_s: float = 300, hours: float = 24):
        super().__init__(name="calendar", daemon=True)
        self.robot, self.bus, self.every_s, self.hours = robot, bus, every_s, hours
        self.stop_evt = threading.Event()

    def tick(self):
        res = self.robot.calendar("list", {"days_ahead": self.hours / 24})
        if "events" in res:
            self.bus("calendar", {"type": "calendar_upcoming", "hours": self.hours, **res})
        else:
            self.bus("calendar", {"type": "calendar_error", "reason": res.get("reason", "api_error"),
                                  "error": res.get("error", "unknown error")})

    def run(self):
        while not self.stop_evt.is_set():
            self.tick()
            self.stop_evt.wait(self.every_s)

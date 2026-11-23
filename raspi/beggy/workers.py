"""Background workers of the control board. Each runs in its own thread and reports through `bus`
(a callable: bus(topic_name, payload_dict) -> publishes to MQTT and keeps the latest value)."""
from __future__ import annotations

import collections
import logging
import queue
import subprocess
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
            from openwakeword.model import Model
            wake = Model(wakeword_models=[self.settings.get()["wakeword"]], inference_framework="onnx")
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
                score = max(wake.predict(c).values())
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
class BatteryWorker(threading.Thread):
    def __init__(self, settings, bus):
        super().__init__(name="battery", daemon=True)
        self.settings, self.bus = settings, bus
        self.stop_evt = threading.Event()
        self.alerted = None

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
            level = "ok"
            if not r["plugged_in"]:
                if r["min_cell_v"] <= rt["battery_critical_cell_v"]:
                    level = "critical"
                elif r["min_cell_v"] <= rt["battery_low_cell_v"]:
                    level = "low"
            self.bus("battery", {"type": "battery_status", "level": level, **r})
            if level != "ok" and self.alerted != level:
                self.bus("battery", {"type": f"battery_{level}", "percent": r["percent"], "min_cell_v": r["min_cell_v"]})
            self.alerted = level if level != "ok" else None
            if level == "critical" and rt["battery_auto_shutdown"]:
                log.critical("battery critical - shutting down")
                ups.power_off()
                subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)
                return
            self.stop_evt.wait(rt["battery_every_s"])

"""Background workers of the control board. Each runs in its own thread and reports through `bus`
(a callable: bus(topic_name, payload_dict) -> publishes to MQTT and keeps the latest value)."""
from __future__ import annotations

import collections
import concurrent.futures
import logging
import queue
import threading
import time

import numpy as np

from beggy.settings import ROOT

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
        source = open_source(self.env.camera_source, pcfg.width, pcfg.height)
        detector = PoseDetector(pcfg.model_path)
        # servos last: if the camera fails, the head is never powered up (hwpwm keeps pulsing after a crash)
        head = make_head(hcfg)
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
FOLLOW_UP_S = 6.0   # after Beggy asks a question, wait this long for the answer without a wake word


def is_question(reply: str) -> bool:
    """Beggy speaks as a woman: a question ends in "คะ" (a statement in "ค่ะ" / "นะคะ")."""
    r = reply.strip().rstrip(" .!")
    if r.endswith("?") or (r.endswith("คะ") and not r.endswith("นะคะ")):
        return True
    return any(w in r for w in QUESTION_WORDS)


QUESTION_WORDS = ("ขอทราบ", "ไหม", "มั้ย", "อะไร", "เมื่อไร", "เมื่อไหร่", "กี่โมง", "ที่ไหน", "ใคร",
                  "หรือเปล่า", "หรือไม่", "ยังไง", "อย่างไร", "แบบไหน", "ช่วยบอก", "ยืนยัน")


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
            from beggy.local_stt import make_local_stt
            self.stt, self.assistant = Stt(make_local_stt(self.env.whisper_model)), Assistant(self.robot)

    def handle_audio(self, wav: bytes, source: str = "voice", speak: bool = True) -> dict:
        """Recorded speech (16 kHz wav) -> reply. Used by the mic loop and POST /ask_audio.

        Fast path: Gemini answers straight from the audio while STT runs at the same time (the transcript
        is only for logs / MQTT and to throw the answer away when nobody spoke). If the audio request
        fails, the old way is used: transcript (Gemini or offline Whisper) -> text question."""
        audio_s = round((len(wav) - 44) / 32000, 1)   # 16 kHz mono 16-bit
        self._keep_for_debug(wav)
        try:
            self._ensure_ai()
            with self.busy:
                self._turn_speak, self._turn_announced = speak, False
                pool = concurrent.futures.ThreadPoolExecutor(1)
                t0 = time.time()
                stt_took = {}

                def timed_stt():
                    text = self.stt.transcribe(wav)
                    stt_took["s"] = round(time.time() - t0, 2)   # when STT really finished (for the log)
                    return text
                stt_job = pool.submit(timed_stt)
                pool.shutdown(wait=False)   # the job keeps running; we just never wait for a slow STT
                reply, think_s = None, 0.0
                try:
                    reply = self.assistant.ask(audio=wav)
                    think_s = round(time.time() - t0, 2)
                except Exception as e:  # noqa: BLE001 - fall back to the text path below
                    log.warning("Gemini audio question failed (%s) - using the transcript", str(e)[:100])
                if reply is not None:
                    # Gemini already answered from the audio: don't keep the user waiting for the transcript
                    # (only needed for logs / MQTT and to drop an answer to noise). Late ones are logged later.
                    try:
                        text = stt_job.result(timeout=1.0)
                    except concurrent.futures.TimeoutError:
                        stt_job.add_done_callback(lambda job: self._late_transcript(job, source, t0))
                        return self._finish("(transcript pending)", reply, think_s, source, speak)
                    except Exception:  # noqa: BLE001
                        log.warning("STT failed, but Gemini already answered from the audio", exc_info=True)
                        text = "(no transcript)"
                else:
                    text = stt_job.result()
                stt = {"stt_s": stt_took.get("s", round(time.time() - t0, 2)), "stt_model": self.stt.last_model,
                       "audio_s": audio_s}
                if not text:
                    out = {"type": "no_speech", "source": source, **stt}
                    self.bus("voice", out)
                    return out
                self.bus("voice", {"type": "transcript", "source": source, "text": text, **stt})
                if reply is None:
                    return {**self._answer(text, source, speak), **stt}
                self.assistant.set_last_question(text)
                return {**self._finish(text, reply, think_s, source, speak), **stt}
        except Exception as e:   # network / quota problems must not kill the robot
            log.exception("voice pipeline failed")
            out = {"type": "voice_error", "source": source, "error": str(e)[:300]}
            self.bus("voice", out)
            if speak:
                self.say_error(e)
            return out

    DEBUG_DIR = ROOT / "voice_debug"
    DEBUG_KEEP = 20

    PROGRESS_SAY = {
        "history": "รอสักครู่นะคะ เบกกี้กำลังดูข้อมูลย้อนหลังให้ค่ะ",
    }

    def progress(self, kind: str) -> None:
        """Called by a slow tool: say what Beggy is doing (once per spoken question, without blocking it).
        The final answer waits for this to finish playing (Speaker's play lock)."""
        text = self.PROGRESS_SAY.get(kind)
        if not text or not getattr(self, "_turn_speak", False) or getattr(self, "_turn_announced", False):
            return
        self._turn_announced = True
        self.bus("voice", {"type": "progress", "kind": kind, "text": text})
        voice = self.settings.get()["tts_voice"]
        threading.Thread(target=self._say_quietly, args=(text, voice), name="progress", daemon=True).start()

    def _prepare_progress(self):
        """Synthesize the "please wait" phrases at start-up so the first one plays instantly."""
        voice = self.settings.get()["tts_voice"]
        for text in self.PROGRESS_SAY.values():
            try:
                self.speaker.prepare(text, voice)
            except Exception as e:  # noqa: BLE001 - no internet yet: they'll be made on first use
                log.info("could not prepare progress phrase: %s", str(e)[:80])
                return

    def _say_quietly(self, text, voice):
        try:
            self.speaker.say(text, voice, cache=True)
        except Exception:  # noqa: BLE001 - a missed "please wait" must not break the answer
            log.exception("progress message failed")

    def _keep_for_debug(self, wav: bytes) -> None:
        """Keep the last few recordings (voice_debug/, gitignored) to check what Gemini actually heard."""
        try:
            self.DEBUG_DIR.mkdir(exist_ok=True)
            (self.DEBUG_DIR / f"q_{time.strftime('%Y%m%d_%H%M%S')}.wav").write_bytes(wav)
            for old in sorted(self.DEBUG_DIR.glob("q_*.wav"))[:-self.DEBUG_KEEP]:
                old.unlink()
        except OSError:
            pass

    def _late_transcript(self, job, source, t0) -> None:
        try:
            text = job.result()
        except Exception as e:  # noqa: BLE001
            log.warning("late STT failed: %s", str(e)[:100])
            return
        self.bus("voice", {"type": "transcript", "source": source, "text": text, "late": True,
                           "stt_s": round(time.time() - t0, 2), "stt_model": self.stt.last_model})

    def say_error(self, e: Exception) -> None:
        """Tell the user out loud, so a failed question isn't just silence."""
        from beggy.assistant import RETRYABLE
        busy = any(k in str(e) for k in RETRYABLE)
        text = ("ตอนนี้ระบบคิดของเบกกี้ไม่ว่าง ลองถามใหม่อีกทีนะคะ" if busy
                else "ขอโทษค่ะ เบกกี้มีปัญหานิดหน่อย ลองถามใหม่อีกครั้งนะคะ")
        try:
            self.speaker.say(text, self.settings.get()["tts_voice"])
        except Exception:
            log.exception("could not speak the error message")

    def handle_text(self, text: str, source: str = "voice", speak: bool = True) -> dict:
        """Text -> Gemini -> reply (spoken). Also used by the API / MQTT 'ask' command."""
        with self.busy:
            self._ensure_ai()
            self._turn_speak, self._turn_announced = speak, False
            return self._answer(text, source, speak)

    def _answer(self, text, source, speak) -> dict:
        t0 = time.time()
        r = self.assistant.ask(text)
        return self._finish(text, r, round(time.time() - t0, 2), source, speak)

    def _finish(self, text, r, think_s, source, speak) -> dict:
        out = {"type": "voice_reply", "source": source, "transcript": text, **r, "think_s": think_s}
        if speak and r["reply"]:
            out["tts"] = self.speaker.say(r["reply"], self.settings.get()["tts_voice"])
        self.bus("voice", out)
        return out

    def run(self):
        from voice.audio import Mic, MicStalled, rms, to_wav
        from voice.config import VoiceConfig
        from voice_main import record_utterance

        vcfg = VoiceConfig(device=self.env.mic_device)
        if hasattr(self.speaker, "prepare"):
            threading.Thread(target=self._prepare_progress, name="tts-prepare", daemon=True).start()
        wake = None
        if self.env.wake_mode == "wake":
            from voice.wake import make_wake
            wake = make_wake(self.settings.get()["wakeword"])   # built-in name or models/hey_beggy.joblib
        else:
            log.info("WAKE_MODE=%s: voice only through POST /ask or the 'ask' command", self.env.wake_mode)
            self.reported_dead = True   # not an error: there is just no mic loop
            return
        levels = collections.deque(maxlen=int(3 * vcfg.rate / vcfg.chunk))
        recent = collections.deque(maxlen=int(1.0 * vcfg.rate / vcfg.chunk))   # pre-roll for the question
        while not self.stop_evt.is_set():   # reopen the mic if it stalls (USB hiccup) instead of hanging
            try:
                with Mic(vcfg.device, vcfg.mic_rate, vcfg.rate, vcfg.chunk) as mic:
                    log.info("listening for wake word '%s'", self.settings.get()["wakeword"])
                    last_near = 0.0
                    while not self.stop_evt.is_set():
                        c = mic.read()
                        levels.append(rms(c))
                        recent.append(c)
                        rt = self.settings.get()
                        if not rt["voice_enabled"]:
                            continue
                        score = wake.predict(c)
                        if score < rt["wake_threshold"]:
                            if score >= 0.2 and time.monotonic() - last_near > 1.0:   # helps tuning the threshold
                                last_near = time.monotonic()
                                log.info("wake near miss: score %.2f < threshold %.2f", score, rt["wake_threshold"])
                            continue
                        self.bus("voice", {"type": "wake", "score": round(float(score), 3)})
                        noise = float(np.median(levels)) if levels else 100.0
                        audio = record_utterance(mic, vcfg, noise, preroll=list(recent))
                        if audio is None:
                            self.bus("voice", {"type": "no_speech"})
                        else:
                            out = self.handle_audio(to_wav(audio, vcfg.rate))
                            for _ in range(3):   # Beggy asked something back: listen for the answer, no wake word
                                if out.get("type") != "voice_reply" or not is_question(out.get("reply", "")):
                                    break
                                mic.flush()      # drop Beggy's own voice
                                self.bus("voice", {"type": "follow_up"})
                                audio = record_utterance(mic, vcfg, noise, start_timeout_s=FOLLOW_UP_S)
                                if audio is None:
                                    break
                                out = self.handle_audio(to_wav(audio, vcfg.rate))
                        recent.clear()
                        mic.flush()
                        wake.reset()
                        end = time.time() + vcfg.wake_cooldown_s
                        while time.time() < end:   # warm the detector up meanwhile, but ignore its scores
                            c = mic.read()
                            recent.append(c)
                            wake.predict(c)
            except MicStalled as e:
                log.warning("%s - reopening the mic", e)
                time.sleep(2)
            except Exception:  # noqa: BLE001 - e.g. the device still busy right after a stall: keep retrying
                log.exception("mic error - retrying in 5 s")
                self.stop_evt.wait(5)


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
        if hasattr(self.robot, "_refresh_cal_cache"):   # keep Gemini's 7-day calendar view warm too
            self.robot._refresh_cal_cache()
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

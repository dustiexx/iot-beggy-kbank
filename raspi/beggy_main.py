#!/usr/bin/env python3
"""Beggy control board server (Raspberry Pi 5).

Runs everything on the Pi in one process:
  vision  - camera -> MediaPipe posture (edge) + pan/tilt head centering
  voice   - wake word -> STT (Gemini) -> thinking (Gemini + tools) -> TTS (edge-tts) -> speaker
  battery - UPS HAT (E) over I2C
  mqtt    - publishes beggy/pi/*, receives sensor board data, commands and settings
  calendar- Google Calendar for Gemini + upcoming events on beggy/pi/calendar (for deadline notifications)
  api     - small HTTP API (default port 8080)

    python beggy_main.py                    # everything
    python beggy_main.py --no-vision        # e.g. while the camera is unplugged
    python beggy_main.py --no-voice

API:
    GET  /status      latest posture / battery / environment / head / voice + fps
    GET  /settings    runtime settings
    POST /settings    {"posture_bad_after_s": 5, ...}
    POST /cmd         {"action": "center" | "home" | "say", "text": "..."}   (local testing only, not on MQTT)
    POST /ask         {"text": "...", "speak": true}   type to Beggy instead of talking
    POST /ask_audio   body = 16 kHz mono wav (?speak=0 to stay quiet)   same path as the mic: STT -> Gemini -> TTS
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from beggy.mqtt import MqttLink
from beggy.settings import ROOT, Env, Settings
from beggy.tts import Speaker
from beggy.workers import BatteryWorker, CalendarWorker, VisionWorker, VoiceWorker

log = logging.getLogger("beggy")


class Robot:
    """Shared state + the actions Gemini tools, the API and MQTT commands can trigger."""

    def __init__(self, env, settings, mqtt):
        self.env, self.settings, self.mqtt = env, settings, mqtt
        self.latest: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.vision: VisionWorker | None = None
        self.voice: VoiceWorker | None = None
        self.started = time.time()

    # everything any worker reports goes through here
    def bus(self, name: str, payload: dict, publish: bool = True):
        payload.setdefault("unix_ms", int(time.time() * 1000))
        with self.lock:
            self.latest.setdefault(name, {})[payload.get("type", name)] = payload
        if publish:
            self.mqtt.publish(name, dict(payload))
        log.info("%s %s", name, json.dumps(payload, ensure_ascii=False)[:300])

    def _get(self, name, typ=None):
        with self.lock:
            d = self.latest.get(name, {})
            if typ:
                return d.get(typ)
            return max(d.values(), key=lambda p: p.get("unix_ms", 0)) if d else None

    # --- tool / command actions ---
    def center_head(self):
        if not self.vision:
            return {"status": "error", "error": "camera not running"}
        return self.vision.request("center")

    def head_home(self):
        if not self.vision:
            return {"status": "error", "error": "camera not running"}
        return self.vision.request("home")

    def environment(self):
        env = self._get("esp")
        if not env:
            return {"error": "no data from the sensor board yet"}
        age = (time.time() * 1000 - env.get("unix_ms", 0)) / 1000
        return {**{k: v for k, v in env.items() if k not in ("unix_ms", "type", "device")}, "age_s": round(age)}

    def posture(self):
        return self._get("posture", "posture_status") or self._get("posture") or {"state": "unknown"}

    def battery(self):
        b = self._get("battery", "battery_status")
        if not b:
            return {"error": "battery status not available"}
        return {k: b[k] for k in ("percent", "charging", "plugged_in", "minutes_to_empty", "minutes_to_full")}

    def history(self, kind, args):
        from beggy.history import History
        if not hasattr(self, "_history"):
            self._history = History()
        if kind == "posture":
            return self._history.posture(args.get("period", "today"))
        return self._history.environment(args.get("metric", ""), args.get("period", "today"))

    def calendar(self, op, args):
        """Calendar actions for Gemini tools / the feed. Errors come back as
        {"error": message, "reason": not_connected | login_expired | api_error}."""
        from google.auth.exceptions import RefreshError

        from beggy.gcal import Calendar, NotConnected
        if getattr(self, "_cal", None) is None:
            self._cal = Calendar()
        try:
            if op == "list":
                return {"events": self._cal.list_events(float(args.get("days_ahead", 1)))}
            self._cal_cache = None    # any change: Gemini's view of the calendar must be refreshed
            if op == "create":
                ev = self._cal.create_event(args["title"], args["start"], args["end"])
                self.bus("calendar", {"type": "calendar_created", **ev})
                return {"created": ev}
            if op == "update":
                ev = self._cal.update_event(args["event_id"], args.get("title"), args.get("start"), args.get("end"))
                self.bus("calendar", {"type": "calendar_updated", **ev})
                return {"updated": ev}
            if op == "delete":
                ev = self._cal.delete_event(args["event_id"])
                self.bus("calendar", {"type": "calendar_deleted", **ev})
                return {"deleted": ev}
            return {"error": f"unknown calendar op {op}", "reason": "api_error"}
        except NotConnected as e:
            self._cal = None          # pick up a fresh login next time, no restart needed
            return {"error": str(e), "reason": e.reason}
        except RefreshError as e:     # token died while the service was already built
            self._cal = None
            return {"error": "Google Calendar login expired (run: python -m beggy.gcal login)",
                    "reason": "login_expired", "detail": str(e)[:120]}
        except Exception as e:   # bad dates from the model, network, API errors -> let Gemini explain
            log.warning("calendar %s failed: %s", op, e)
            return {"error": str(e)[:200], "reason": "api_error"}

    CAL_CACHE_S = 120

    def calendar_context(self) -> list | None:
        """The next 7 days of events (with ids) for Gemini's instructions, so it can answer / edit / delete
        without a list_calendar_events round trip first. Cached; cleared whenever Beggy changes an event."""
        cache = getattr(self, "_cal_cache", None)
        if cache and time.time() - cache[0] >= self.CAL_CACHE_S:   # old: answer now, refresh in the background
            threading.Thread(target=self._refresh_cal_cache, name="cal-refresh", daemon=True).start()
        return cache[1] if cache else self._refresh_cal_cache()

    def _refresh_cal_cache(self):
        events = self.calendar("list", {"days_ahead": 7}).get("events")
        self._cal_cache = (time.time(), events) if events is not None else None
        return events

    def status(self):
        with self.lock:
            latest = json.loads(json.dumps(self.latest))
        return {"uptime_s": round(time.time() - self.started), "mqtt_connected": self.mqtt.connected,
                "vision_fps": round(self.vision.fps, 1) if self.vision else None, "latest": latest}

    def command(self, data: dict) -> dict:
        action = data.get("action")
        if action == "center":
            return self.center_head()
        if action == "home":
            return self.head_home()
        if action == "say":
            sp = Speaker(self.env.audio_out, ROOT / "tts_out", gain_db=self.env.audio_gain_db)
            return sp.say(str(data.get("text", "")), self.settings.get()["tts_voice"])
        if action == "ask":
            return self.ask(str(data.get("text", "")), speak=data.get("speak", True), source="api")
        return {"error": f"unknown action {action!r}"}

    def ask_audio(self, wav: bytes, speak=True):
        if not self.voice:
            return {"error": "voice disabled"}
        return self.voice.handle_audio(wav, source="api", speak=speak)

    def ask(self, text, speak=True, source="api"):
        if not self.voice:
            return {"error": "voice disabled"}
        try:
            return self.voice.handle_text(text, source=source, speak=speak)
        except Exception as e:
            log.exception("ask failed")
            return {"type": "voice_error", "error": str(e)[:300]}


def make_api(robot: Robot, settings: Settings):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return None

        def do_GET(self):
            if self.path == "/status":
                return self._send(200, robot.status())
            if self.path == "/settings":
                return self._send(200, settings.get())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path.split("?")[0] == "/ask_audio":
                wav = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if wav[:4] != b"RIFF":
                    return self._send(400, {"error": "body must be a wav file"})
                return self._send(200, robot.ask_audio(wav, speak="speak=0" not in self.path))
            data = self._body()
            if data is None:
                return self._send(400, {"error": "body must be JSON"})
            if self.path == "/settings":
                return self._send(200, settings.update(data))
            if self.path == "/cmd":
                return self._send(200, robot.command(data))
            if self.path == "/ask":
                return self._send(200, robot.ask(str(data.get("text", "")), speak=data.get("speak", True)))
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            log.debug("api " + fmt, *args)

    return Handler


def main() -> int:
    p = argparse.ArgumentParser(description="Beggy control board")
    p.add_argument("--no-vision", action="store_true")
    p.add_argument("--no-voice", action="store_true")
    p.add_argument("--no-battery", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)-9s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    env = Env.load()
    settings = Settings()
    mqtt = MqttLink(env)
    robot = Robot(env, settings, mqtt)

    # inbound MQTT
    mqtt.subscribe(f"{env.topic_prefix}/esp/+", lambda t, d: robot.bus("esp", {"type": "env", **d}, publish=False))
    # Sensor board data is only cached (the broker already has it).
    # The reply goes to settings_result - never back to beggy/pi/settings, or we'd receive our
    # own reply as new settings and loop forever.
    mqtt.subscribe(mqtt.t_settings, lambda t, d: robot.bus(
        "settings_result", {"type": "settings_applied", **settings.update(d)}))
    mqtt.start()

    workers = []
    if not args.no_vision:
        robot.vision = VisionWorker(env, settings, robot.bus)
        workers.append(robot.vision)
    if not args.no_voice:
        robot.voice = VoiceWorker(env, settings, robot.bus, robot, Speaker(env.audio_out, ROOT / "tts_out", gain_db=env.audio_gain_db))
        workers.append(robot.voice)
    workers.append(CalendarWorker(robot, robot.bus))
    if not args.no_battery and env.ups_enabled:
        speaker = Speaker(env.audio_out, ROOT / "tts_out", gain_db=env.audio_gain_db)
        workers.append(BatteryWorker(settings, robot.bus,
                                     say=lambda text: speaker.say(text, settings.get()["tts_voice"])))
    for w in workers:
        w.start()

    api = ThreadingHTTPServer(("0.0.0.0", env.api_port), make_api(robot, settings))
    threading.Thread(target=api.serve_forever, name="api", daemon=True).start()
    log.info("API on http://0.0.0.0:%d  (MQTT %s:%d)", env.api_port, env.mqtt_host, env.mqtt_port)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    last_hb = 0.0
    while not stop.wait(1):
        if time.time() - last_hb >= 60:   # heartbeat for the dashboard's "online" indicator
            mqtt.publish("status", {"type": "status", "online": True, "uptime_s": round(time.time() - robot.started),
                                    "vision_fps": round(robot.vision.fps, 1) if robot.vision else None}, retain=True)
            last_hb = time.time()
        for w in workers:
            if not w.is_alive() and not getattr(w, "reported_dead", False):
                log.error("worker %s stopped", w.name)
                w.reported_dead = True

    log.info("shutting down")
    # If something (mic, camera, a library thread) keeps the process alive, print every thread's stack
    # to the journal and exit after 10 s instead of making systemd wait 90 s and SIGKILL us.
    faulthandler.dump_traceback_later(10, exit=True)
    for w in workers:
        w.stop_evt.set()
    api.shutdown()
    mqtt.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""STT + thinking with Gemini.

STT:      audio clip -> Thai text   (light model, separate free quota)
Thinking: text -> reply             (main model, can call tools: robot head, sensors, battery, posture, calendar)
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("assistant")

STT_MODEL = "gemini-3.1-flash-lite"
# Tried in order. Free tier (Nov 2026): flash-lite 15 RPM / 500 RPD and ~2-5 s per answer;
# 3.6 / 3.8 flash only 5 RPM / 20 RPD and 13-55 s or 503/504 - so lite only. Add a fallback
# with GEMINI_MODEL=gemini-3.1-flash-lite,gemini-3.6-flash if you accept spending its 20/day.
CHAT_MODELS = ["gemini-3.1-flash-lite"]

STT_PROMPT = ("Transcribe this audio exactly as spoken. The speaker uses Thai, sometimes with English words. "
              "Output only the transcript text, nothing else. If there is no clear speech, output nothing.")

SYSTEM_PROMPT = """\
คุณคือ "เบกกี้" (Beggy) หุ่นยนต์ตั้งโต๊ะตัวเล็กที่ช่วยดูท่านั่ง สภาพแวดล้อมโต๊ะทำงาน และตารางนัดของผู้ใช้
- ตอบเป็นภาษาไทย สุภาพ เป็นกันเอง สั้นไม่เกิน 2 ประโยค เพราะคำตอบจะถูกอ่านออกเสียง ห้ามใช้ markdown อีโมจิ หรือรายการหัวข้อ
- ถ้าผู้ใช้ขอให้หันมาหา มองมาที่ตัวเอง หรือจัดกล้องให้ตรง ให้เรียก center_on_person
- ถ้าผู้ใช้ถามอุณหภูมิ ความชื้น แสง ฝุ่น ให้เรียก get_environment แล้วตอบด้วยตัวเลขจริง
- ถ้าถามเรื่องท่านั่ง ให้เรียก get_posture; ถามเรื่องแบตเตอรี่ ให้เรียก get_battery
- เรื่องตารางนัด ให้ใช้เครื่องมือ calendar ถ้าเครื่องมือตอบว่ายังไม่ได้เชื่อมต่อ ให้บอกผู้ใช้ตรง ๆ
- ห้ามเดาค่าที่ไม่รู้
"""


def _client():
    from google import genai
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set - put it in raspi/.env")
    return genai.Client(api_key=key)


class Stt:
    def __init__(self):
        self.client = _client()
        self.model = os.environ.get("GEMINI_STT_MODEL", STT_MODEL)

    def transcribe(self, wav: bytes) -> str:
        from google.genai import types
        r = self.client.models.generate_content(
            model=self.model,
            contents=[STT_PROMPT, types.Part.from_bytes(data=wav, mime_type="audio/wav")],
        )
        return (r.text or "").strip()


def make_tools(robot):
    """Python functions Gemini may call (automatic function calling). Docstrings are what Gemini reads."""

    def center_on_person() -> dict:
        """Turn Beggy's camera head (pan/tilt servos) so the user is in the middle of the camera view."""
        return robot.center_head()

    def head_home() -> dict:
        """Move Beggy's head back to its normal position (the position used for posture checking)."""
        return robot.head_home()

    def get_environment() -> dict:
        """Latest desk environment readings from the sensor board: temperature (C), humidity (%), light (lux), dust (PM2.5 ug/m3)."""
        return robot.environment()

    def get_posture() -> dict:
        """Current sitting posture of the user: state good/bad/away, the issues found, and how long it has been bad."""
        return robot.posture()

    def get_battery() -> dict:
        """Beggy's battery: percent, charging or not, minutes left."""
        return robot.battery()

    def list_calendar_events(days_ahead: int) -> dict:
        """List the user's Google Calendar events from now until `days_ahead` days ahead."""
        return robot.calendar("list", {"days_ahead": days_ahead})

    def create_calendar_event(title: str, start_iso: str, end_iso: str) -> dict:
        """Create a Google Calendar event. start_iso / end_iso are ISO 8601 times in Asia/Bangkok, e.g. 2026-11-24T15:00:00+07:00."""
        return robot.calendar("create", {"title": title, "start": start_iso, "end": end_iso})

    return [center_on_person, head_home, get_environment, get_posture, get_battery,
            list_calendar_events, create_calendar_event]


RETRYABLE = ("503", "504", "429", "UNAVAILABLE", "DEADLINE", "RESOURCE_EXHAUSTED", "timed out", "Timeout")


class Assistant:
    """Keeps a short conversation; starts fresh after `idle_reset_s` of silence.

    Models are tried in order: if one is overloaded (503), too slow (504 / timeout) or out of
    free quota (429), the same conversation continues on the next one."""

    def __init__(self, robot, idle_reset_s: float = 300, timeout_s: float = 20):
        self.client = _client()
        self.models = [m.strip() for m in os.environ.get("GEMINI_MODEL", ",".join(CHAT_MODELS)).split(",") if m.strip()]
        self.tools = make_tools(robot)
        self.idle_reset_s = idle_reset_s
        self.timeout_ms = int(timeout_s * 1000)
        self.chat = None
        self.history = []
        self.last = 0.0

    def _make_chat(self, model):
        from google.genai import types
        now = time.strftime("%A %Y-%m-%d %H:%M", time.localtime())
        return self.client.chats.create(
            model=model,
            history=self.history,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT + f"\nเวลาปัจจุบัน (Asia/Bangkok): {now}",
                tools=self.tools,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                http_options=types.HttpOptions(timeout=self.timeout_ms),
            ),
        )

    def ask(self, text: str) -> dict:
        if time.time() - self.last > self.idle_reset_s:
            self.history = []
        self.last = time.time()
        errors = []
        for model in self.models:
            chat = self._make_chat(model)
            try:
                r = chat.send_message(text)
            except Exception as e:
                msg = str(e)
                if any(k in msg for k in RETRYABLE):
                    log.warning("%s unavailable (%s), trying next model", model, msg[:80])
                    errors.append(f"{model}: {msg[:80]}")
                    continue
                raise
            new = chat.get_history()[len(self.history):]   # this turn only
            self.history = chat.get_history()
            calls = [p.function_call.name for h in new for p in (h.parts or []) if p.function_call]
            return {"reply": (r.text or "").strip(), "tools_called": calls, "model": model}
        raise RuntimeError("all Gemini models unavailable: " + " | ".join(errors))

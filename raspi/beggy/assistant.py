"""STT + thinking with Gemini.

STT:      audio clip -> Thai text   (light model, separate free quota)
Thinking: text -> reply             (main model, can call tools: robot head, sensors, battery, posture, calendar)
"""
from __future__ import annotations

import functools
import json
import logging
import os
import queue
import threading
import time

log = logging.getLogger("assistant")

# Tried in order, lite models only. Free tier (Nov 2026): 3.1 flash-lite 15 RPM / 500 RPD, ~2-5 s per answer,
# but it sometimes answers 503 "high demand" for a while: then the next lite model takes over.
# 3.6 / 3.8 flash are only 5 RPM / 20 RPD and slow - add them yourself (GEMINI_MODEL=...) if you accept that.
LITE_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]   # 2.5 flash-lite was retired (404, 27 Nov 2026)
STT_MODELS = LITE_MODELS     # override with GEMINI_STT_MODEL (comma list)
CHAT_MODELS = LITE_MODELS    # override with GEMINI_MODEL (comma list)


def _models(env_name: str, default: list[str]) -> list[str]:
    return [m.strip() for m in os.environ.get(env_name, ",".join(default)).split(",") if m.strip()]


def _thinking(model: str):
    """Least thinking = fastest answers. Gemini 2.x uses a token budget, 3.x a level."""
    from google.genai import types
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level="minimal")

STT_PROMPT = ("Transcribe this audio exactly as spoken. The speaker uses Thai, sometimes with English words. "
              "Output only the transcript text, nothing else. If there is no clear speech, output nothing. "
              "Leave out a wake word at the start (\"hey Beggy\", \"hey Jarvis\"). "
              "Thai times are words, e.g. บ่ายโมง (13:00), บ่ายสอง, ห้าโมงเย็น, สองทุ่ม, ตีห้า - write them as spoken.")

AUDIO_QUESTION = "(นี่คือเสียงคำถามของผู้ใช้ ตอบสิ่งที่ผู้ใช้พูดในเสียงนี้ ตอนต้นอาจมีคำปลุก เช่น \"hey Beggy\" หรือ \"hey Jarvis\" ติดมา ไม่ต้องสนใจคำนั้น)"

SYSTEM_PROMPT = """\
คุณคือ "เบกกี้" (Beggy) หุ่นยนต์ตั้งโต๊ะตัวเล็กที่ช่วยดูท่านั่ง สภาพแวดล้อมโต๊ะทำงาน และตารางนัดของผู้ใช้
- ตอบเป็นภาษาไทย สุภาพ เป็นกันเอง สั้นไม่เกิน 2 ประโยค เพราะคำตอบจะถูกอ่านออกเสียง ห้ามใช้ markdown อีโมจิ หรือรายการหัวข้อ
- เบกกี้พูดด้วยเสียงผู้หญิง: ลงท้ายด้วย ค่ะ / คะ / นะคะ เสมอ และเรียกตัวเองว่า "เบกกี้" (ไม่ใช้ ผม หรือ ครับ)
- ถ้าผู้ใช้ขอให้หันมาหา มองมาที่ตัวเอง หรือจัดกล้องให้ตรง ให้เรียก center_on_person
- ถ้าผู้ใช้ถามอุณหภูมิ ความชื้น แสง ฝุ่น ให้เรียก get_environment แล้วตอบด้วยตัวเลขจริง
- ถ้าถามเรื่องท่านั่ง ให้เรียก get_posture; ถามเรื่องแบตเตอรี่ ให้เรียก get_battery
- ถ้าถามย้อนหลัง (วันนี้ทั้งวัน เมื่อวาน สัปดาห์นี้) ให้ใช้ get_posture_history / get_environment_history
  แล้วสรุปเป็นคำพูดง่าย ๆ เช่น "วันนี้นั่งหลังค่อมรวม 21 นาที" ไม่ต้องอ่านตัวเลขทุกตัว
- เรื่องตารางนัด ให้ใช้เครื่องมือ calendar ถ้าเครื่องมือตอบว่ายังไม่ได้เชื่อมต่อ ให้บอกผู้ใช้ตรง ๆ
- เลื่อนเวลาหรือเปลี่ยนชื่อนัด ให้ใช้ update_calendar_event ครั้งเดียว (ห้ามลบแล้วสร้างใหม่)
- ก่อนลบนัด ให้ถามยืนยันชื่อและเวลานัดกับผู้ใช้ก่อนเสมอ; เวลาที่สร้างนัดให้ใช้เขตเวลา Asia/Bangkok (+07:00)
- ถ้าผู้ใช้ไม่บอกเวลาจบ ให้นัดยาว 1 ชั่วโมง
- เวลาสองค่าติดกัน เช่น "เที่ยง บ่ายสอง" หรือ "บ่ายโมง บ่ายสาม" คือ เวลาเริ่ม ถึง เวลาจบ (12:00-14:00, 13:00-15:00)
- ห้ามเดาเวลานัดเด็ดขาด ถ้าผู้ใช้ยังไม่ได้บอกเวลา (หรือประโยคขาดหาย) ให้ถามกลับว่า "นัดกี่โมงดีคะ"
- การบอกเวลาแบบไทย: เที่ยง/เที่ยงตรง = 12:00, บ่ายโมง = 13:00, บ่ายสอง = 14:00 ... บ่ายสามโมง/บ่ายสาม = 15:00,
  สี่โมงเย็น = 16:00, ห้าโมงเย็น = 17:00, หกโมงเย็น = 18:00, หนึ่งทุ่ม = 19:00 ... ห้าทุ่ม = 23:00, เที่ยงคืน = 00:00,
  ตีหนึ่ง = 01:00 ... ตีห้า = 05:00, หกโมงเช้า = 06:00 ... สิบเอ็ดโมง = 11:00; "บ่าย" ที่ไม่มีเลขต่อท้ายตรง ๆ เช่น "บ่ายโมง" ไม่ใช่เที่ยง
- คำตอบจะถูกอ่านออกเสียง: พูดเวลาเป็นคำแบบคนไทยพูด ห้ามเขียนเป็นตัวเลข เช่น "บ่ายสองโมง ถึง สี่โมงเย็น", "เที่ยงครึ่ง",
  "สองทุ่มสิบห้านาที" (ไม่ใช่ "14:00 ถึง 16:00 น.") และบอกชื่อกับวันของนัดด้วย เพื่อให้ผู้ใช้รู้ทันทีถ้าฟังผิด
- ถ้าข้อมูลไม่ครบและต้องถามกลับ ให้ถามสั้น ๆ และจบด้วยคำถามที่ลงท้ายด้วย "คะ" เช่น "นัดกี่โมงดีคะ" (เบกกี้จะรอฟังคำตอบเอง ผู้ใช้ไม่ต้องเรียกชื่อใหม่)
- ตอบเฉพาะสิ่งที่ถาม เช่น ถามเวลาให้บอกแค่เวลา ("ห้าทุ่มหกนาทีค่ะ") ไม่ต้องบอกวันที่ ถ้าไม่ได้ถาม
- ห้ามบอกว่าสร้าง/แก้/ลบนัดแล้ว ถ้ายังไม่ได้เรียกเครื่องมือปฏิทินจริงในคำถามนี้ และตั้งชื่อนัดตามที่ผู้ใช้พูดในคำถามล่าสุด (ไม่ใช้ชื่อนัดเก่าจากบทสนทนาก่อนหน้า)
- ไม่ต้องปิดท้ายด้วยประโยคชวนคุย เช่น "มีอะไรให้เบกกี้ช่วยอีกไหมคะ" ตอบแค่เรื่องที่ถามแล้วจบ (ถ้าลงท้ายด้วยคำถาม เบกกี้จะรอฟังคำตอบ)
- คำถามทั่วไป (สูตรอาหาร ความรู้ คำแนะนำ คุยเล่น) ให้ตอบเองเลย ห้ามเรียกเครื่องมือ; เรียก get_environment เฉพาะเมื่อถามอุณหภูมิ ความชื้น แสง หรือฝุ่น
- ห้ามเดาค่าที่ไม่รู้
"""


def _client():
    from google import genai
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set - put it in raspi/.env")
    return genai.Client(api_key=key)


class Stt:
    def __init__(self, local=None):
        self.client = _client()
        self.models = _models("GEMINI_STT_MODEL", STT_MODELS)
        self.local = local              # LocalWhisper: offline backup when every Gemini model fails
        self.last_model = None

    def transcribe(self, wav: bytes, timeout_s: float = 10) -> str:
        """Tries each Gemini model in turn when one is overloaded (503), slow or out of quota (free tier),
        then the offline Whisper backup. Any other Gemini error (no internet, bad key) goes straight to it."""
        from google.genai import types

        def call(model):
            return self.client.models.generate_content(
                model=model,
                contents=[STT_PROMPT, types.Part.from_bytes(data=wav, mime_type="audio/wav")],
                config=types.GenerateContentConfig(thinking_config=_thinking(model),
                                                   http_options=types.HttpOptions(timeout=int(timeout_s * 1000))),
            )
        try:
            # no hedging: the reply doesn't wait for the transcript, so a second request would only cost quota
            model, r = hedged(self.models, call, "stt", hedge_after_s=float("inf"))
            self.last_model = model
            return (r.text or "").strip()
        except Exception as e:
            if self.local is None:
                raise
            log.warning("Gemini STT unavailable (%s) - using offline %s", str(e)[:80], self.local.name)
        text = self.local.transcribe(wav)
        self.last_model = self.local.name
        return text


def make_tools(robot):
    """Python functions Gemini may call (automatic function calling). Docstrings are what Gemini reads."""

    def progress(kind: str) -> None:
        """Slow tools (Google Calendar, backend history): Beggy says what she's doing while waiting."""
        hook = getattr(robot, "progress", None)
        if hook:
            hook(kind)

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
        progress("calendar_list")
        return robot.calendar("list", {"days_ahead": days_ahead})

    def create_calendar_event(title: str, start_iso: str, end_iso: str, time_words: str) -> dict:
        """Create a Google Calendar event. start_iso / end_iso are ISO 8601 times in Asia/Bangkok, e.g.
        2026-11-24T15:00:00+07:00. time_words: the user's OWN words for the day/time, copied from what they said
        (e.g. "พรุ่งนี้เที่ยงถึงบ่ายสอง"). If the user did not say a time, do not call this: ask them first."""
        if not (time_words or "").strip():
            return {"error": "the user did not say a time - ask them when (never guess a time)"}
        progress("calendar_create")
        return robot.calendar("create", {"title": title, "start": clean_iso(start_iso), "end": clean_iso(end_iso)})

    def get_posture_history(period: str) -> dict:
        """How the user sat over a past period: seconds of good / bad posture, seconds away,
        number of bad-posture alerts and which issues. period: today, yesterday, last_24_hours,
        this_week or last_7_days."""
        progress("history")
        return robot.history("posture", {"period": period})

    def get_environment_history(metric: str, period: str) -> dict:
        """Past desk environment from the database: average, max (and when), min (and when).
        metric: temperature_c, humidity_pct, light_lux or dust_pm25.
        period: today, yesterday, last_24_hours, this_week or last_7_days."""
        progress("history")
        return robot.history("env", {"metric": metric, "period": period})

    def update_calendar_event(event_id: str, title: str = "", start_iso: str = "", end_iso: str = "",
                              time_words: str = "") -> dict:
        """Change an existing event's title and/or time in one step (leave a field empty to keep it).
        Times are ISO 8601 in Asia/Bangkok, e.g. 2026-11-24T13:00:00+07:00. When changing the time, time_words
        must be the user's OWN words for the new day/time (e.g. "บ่ายสามถึงสี่โมง"); if they did not say the
        new time, do not call this: ask them first. Use this to move / rename an event instead of deleting
        and creating it again."""
        if (start_iso or end_iso) and not (time_words or "").strip():
            return {"error": "the user did not say the new time - ask them (never guess a time)"}
        progress("calendar_update")
        return robot.calendar("update", {"event_id": event_id, "title": title or None,
                                         "start": clean_iso(start_iso) or None, "end": clean_iso(end_iso) or None})

    def delete_calendar_event(event_id: str) -> dict:
        """Delete a Google Calendar event by its id (get the id from list_calendar_events first).
        Only call this after the user clearly confirmed which event to delete."""
        progress("calendar_delete")
        return robot.calendar("delete", {"event_id": event_id})

    return [guard_tool(f) for f in (
        center_on_person, head_home, get_environment, get_posture, get_battery,
        get_posture_history, get_environment_history, list_calendar_events, create_calendar_event,
        update_calendar_event, delete_calendar_event)]


CALENDAR_CHANGES = {"create_calendar_event", "update_calendar_event", "delete_calendar_event"}
NO_FAKE_CHANGES = ("(ระบบ: คำตอบก่อนหน้าบอกว่าจัดการนัดแล้ว แต่ยังไม่ได้เรียกเครื่องมือปฏิทินเลย ปฏิทินจึงยังไม่เปลี่ยน "
                   "ให้เรียก create_calendar_event / update_calendar_event / delete_calendar_event ตอนนี้ "
                   "ถ้าข้อมูลไม่พอให้ถามผู้ใช้แทน ห้ามบอกว่าทำแล้วถ้ายังไม่ได้เรียกเครื่องมือ)")


THAI_MONTHS = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
               "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


_DIGITS = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]


def thai_number(n: int) -> str:
    """1-99 in Thai words (สิบเอ็ด, ยี่สิบ, ...)."""
    tens, ones = divmod(n, 10)
    t = "" if tens == 0 else "สิบ" if tens == 1 else "ยี่สิบ" if tens == 2 else _DIGITS[tens] + "สิบ"
    o = "เอ็ด" if ones == 1 and tens else _DIGITS[ones]
    return t + o


def thai_time(h: int, m: int = 0) -> str:
    """How Thai people say a clock time: 0 เที่ยงคืน, 1-5 ตีหนึ่ง.., 6-11 หกโมงเช้า.., 12 เที่ยง, 13 บ่ายโมง,
    14-15 บ่ายสองโมง.., 16-18 สี่โมงเย็น.., 19-23 หนึ่งทุ่ม..; minutes: ครึ่ง / X นาที."""
    if h == 0:
        base = "เที่ยงคืน"
    elif h <= 5:
        base = "ตี" + thai_number(h)
    elif h <= 11:
        base = thai_number(h) + "โมง" + ("เช้า" if h <= 9 else "")
    elif h == 12:
        base = "เที่ยง"
    elif h == 13:
        base = "บ่ายโมง"
    elif h <= 15:
        base = "บ่าย" + thai_number(h - 12) + "โมง"
    elif h <= 18:
        base = thai_number(h - 12) + "โมงเย็น"
    else:
        base = thai_number(h - 18) + "ทุ่ม"
    if m == 0:
        return base
    return base + ("ครึ่ง" if m == 30 else thai_number(m) + "นาที")


def when(ev: dict, today=None) -> str:
    """Spoken day + time of an event: "พรุ่งนี้ บ่ายโมง ถึง บ่ายสองโมง" / "วันที่ 3 ธันวาคม ทั้งวัน"."""
    import datetime as dt
    try:
        start = dt.datetime.fromisoformat(ev["start"])
    except (KeyError, TypeError, ValueError):
        return ""
    today = today or dt.date.today()
    d = start.date()
    day = {0: "วันนี้", 1: "พรุ่งนี้", 2: "มะรืนนี้"}.get((d - today).days, f"วันที่ {d.day} {THAI_MONTHS[d.month]}")
    if ev.get("all_day") or "T" not in str(ev.get("start")):
        return f"{day} ทั้งวัน"
    try:
        end = dt.datetime.fromisoformat(ev["end"])
        return f"{day} {thai_time(start.hour, start.minute)} ถึง {thai_time(end.hour, end.minute)}"
    except (KeyError, TypeError, ValueError):
        return f"{day} {thai_time(start.hour, start.minute)}"


def clean_iso(value):
    """Gemini sometimes adds a trailing comma / dot / space to an ISO time ("2026-11-27T12:00:00+07:00,")."""
    return value.strip().strip(",.;'\"").strip() if isinstance(value, str) else value


def failed_change(tool: str) -> str:
    verb = {"create_calendar_event": "สร้างนัด", "update_calendar_event": "แก้นัด",
            "delete_calendar_event": "ลบนัด"}[tool]
    return f"ขอโทษค่ะ เบกกี้{verb}ไม่สำเร็จ ลองบอกใหม่อีกครั้งนะคะ"


def describe_change(tool: str, result: dict) -> str:
    """The spoken confirmation for a calendar change, built from what the Calendar API returned."""
    ev = result.get("created") or result.get("updated") or result.get("deleted") or {}
    title = ev.get("title", "")
    verb = {"create_calendar_event": "สร้างนัด", "update_calendar_event": "แก้นัด",
            "delete_calendar_event": "ลบนัด"}[tool]
    middle = " เป็น " if tool == "update_calendar_event" else " "
    return f"เบกกี้{verb}{title}{middle}{when(ev)} ให้แล้วค่ะ".replace("  ", " ")


def claims_calendar_change(reply: str) -> bool:
    """Does the reply say an event was created / changed / deleted?"""
    done = any(w in reply for w in ("ให้แล้ว", "เรียบร้อย", "แล้วนะคะ", "แล้วค่ะ", "สำเร็จ"))
    verb = any(w in reply for w in ("สร้าง", "เพิ่ม", "ลบ", "แก้", "เปลี่ยน", "เลื่อน", "ย้าย"))
    return "นัด" in reply and done and verb


RETRYABLE = ("503", "504", "429", "UNAVAILABLE", "DEADLINE", "RESOURCE_EXHAUSTED", "timed out", "Timeout",
             "404", "NOT_FOUND")   # 404: model retired - skip to the next one

HEDGE_AFTER_S = 5.0   # a model that hasn't answered by then: also ask the next one, first good answer wins


class AllModelsBusy(RuntimeError):
    pass


class _Turn:
    """One question. Only one attempt may run tools (calendar changes must never happen twice)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None      # attempt that started running tools
        self.winner = None     # attempt whose answer is used


class _Attempt:
    def __init__(self, model, turn):
        self.model, self.turn, self.result, self.error = model, turn, None, None


_current = threading.local()   # the attempt running in this thread (tools are called in the same thread)


def guard_tool(fn):
    """Tools refuse to run for an attempt that lost the race, so a slow model that wakes up late can't
    create / delete an event a second time."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        att = getattr(_current, "attempt", None)
        if att is not None:
            turn = att.turn
            with turn.lock:
                if (turn.winner is not None and turn.winner is not att) or \
                        (turn.owner is not None and turn.owner is not att):
                    return {"error": "cancelled: another model is answering this question"}
                turn.owner = att
        return fn(*args, **kwargs)
    return wrapped


def hedged(models: list[str], call, what: str, hedge_after_s: float = HEDGE_AFTER_S):
    """Run call(model) on the first model; if it is slow, also start the next one; on a retryable error
    (503 / 504 / 429 / timeout) start the next one right away. Returns (model, result) of the first good answer
    (the one that ran tools, if any did). Raises the error itself if it is not retryable, AllModelsBusy if all failed."""
    turn, done, errors, started = _Turn(), queue.Queue(), [], []

    def start(model):
        att = _Attempt(model, turn)
        started.append(att)

        def run():
            _current.attempt = att
            try:
                att.result = call(model)
            except Exception as e:  # noqa: BLE001 - handled by the caller thread
                att.error = e
            finally:
                _current.attempt = None
                done.put(att)
        threading.Thread(target=run, name=f"{what}-{model}", daemon=True).start()

    nxt, pending = 1, 1
    start(models[0])
    hedge_at = time.monotonic() + hedge_after_s
    while pending:
        can_hedge = nxt < len(models) and turn.owner is None and hedge_after_s != float("inf")
        try:
            att = done.get(timeout=max(0.0, hedge_at - time.monotonic()) if can_hedge else None)
        except queue.Empty:
            log.warning("%s: %s is slow (> %.0f s), also asking %s", what, started[-1].model, hedge_after_s, models[nxt])
            start(models[nxt])
            nxt, pending, hedge_at = nxt + 1, pending + 1, time.monotonic() + hedge_after_s
            continue
        pending -= 1
        if att.error is None:
            with turn.lock:
                if turn.owner is not None and turn.owner is not att:
                    continue           # the attempt that is running tools gives the answer
                turn.winner = att
            return att.model, att.result
        msg = str(att.error)
        with turn.lock:
            if turn.owner is att:
                turn.owner = None      # it failed mid-way: another model may redo the tool step
        if not any(k in msg for k in RETRYABLE):
            with turn.lock:
                turn.winner = att      # stop the others from running tools
            raise att.error
        log.warning("%s: %s unavailable (%s)", what, att.model, msg[:80])
        errors.append(f"{att.model}: {msg[:80]}")
        if nxt < len(models):          # replace the failed attempt with the next model right away
            start(models[nxt])
            nxt, pending, hedge_at = nxt + 1, pending + 1, time.monotonic() + hedge_after_s
    raise AllModelsBusy("all Gemini models unavailable (503 UNAVAILABLE): " + " | ".join(errors))


class Assistant:
    """Keeps a short conversation; starts fresh after `idle_reset_s` of silence.

    Models are tried in order: if one is overloaded (503), too slow (504 / timeout) or out of
    free quota (429), the same conversation continues on the next one."""

    def __init__(self, robot, idle_reset_s: float = 300, timeout_s: float = 10):
        self.client = _client()
        self.models = _models("GEMINI_MODEL", CHAT_MODELS)
        self.robot = robot
        self.tools = make_tools(robot)
        self.idle_reset_s = idle_reset_s
        self.timeout_ms = int(timeout_s * 1000)
        self.chat = None
        self.history = []
        self._turn_start = None
        self._pending_delete = {}
        self.last = 0.0

    def _make_chat(self, model, history=None):
        from google.genai import types
        now = time.strftime("%A %Y-%m-%d %H:%M", time.localtime())
        extra = ""
        try:
            events = self.robot.calendar_context() if hasattr(self.robot, "calendar_context") else None
        except Exception:  # noqa: BLE001 - the calendar must never block a question
            events = None
        if events is not None:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+07:00", time.localtime())
            compact = [{**{k: e[k] for k in ("id", "title", "start", "end")},
                        **({"past": True} if str(e.get("end", "")) < now_iso else {})} for e in events]
            extra = ("\nนัดตั้งแต่ 00:00 เมื่อวานถึง 7 วันข้างหน้า (ข้อมูลล่าสุด ใช้ id นี้แก้หรือลบได้เลย ไม่ต้องเรียก "
                     "list_calendar_events; \"past\": true = จบไปแล้ว: ถ้าผู้ใช้ถามว่ามีนัดอะไร ให้บอกเฉพาะนัดที่ยังไม่จบ "
                     "เว้นแต่ถามถึงนัดที่ผ่านไปแล้ว แต่แก้หรือลบนัดที่ผ่านไปแล้วได้): " + json.dumps(compact, ensure_ascii=False))
        return self.client.chats.create(
            model=model,
            history=list(self.history if history is None else history),   # a losing attempt can't touch ours
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT + f"\nเวลาปัจจุบัน (Asia/Bangkok): {now}" + extra,
                tools=self.tools,
                # we run the tools ourselves (Assistant._run_tools): only the winning model's calls run, and
                # calendar changes get a ready-made spoken reply instead of a second Gemini round trip
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=_thinking(model),
                http_options=types.HttpOptions(timeout=self.timeout_ms),
            ),
        )

    MAX_ROUNDS = 4

    def ask(self, text: str | None = None, audio: bytes | None = None) -> dict:
        """Text question, or the recorded question itself (16 kHz wav): Gemini listens to it directly, which
        saves a whole speech-to-text round trip. Call set_last_question() with the transcript afterwards.

        Tool calls are run here: create / update / delete get a spoken reply built from the result (no second
        Gemini call); a delete is only done after the user confirmed that event in the previous turn."""
        from google.genai import types
        if time.time() - self.last > self.idle_reset_s:
            self.history = []
            self._pending_delete = {}
        self.last = time.time()
        message = text if audio is None else [types.Part.from_bytes(data=audio, mime_type="audio/wav"),
                                              AUDIO_QUESTION]
        history, calls, reply, model, retried = list(self.history), [], None, None, False
        pending_before = dict(self._pending_delete)
        self._pending_delete = {}
        for _ in range(self.MAX_ROUNDS):
            def call(model, msg=message, hist=history):
                chat = self._make_chat(model, hist)
                return chat, chat.send_message(msg)
            model, (chat, r) = hedged(self.models, call, "chat")
            history = list(chat.get_history())
            fcs = getattr(r, "function_calls", None) or []
            if not fcs:
                reply = (r.text or "").strip()
                if claims_calendar_change(reply) and not CALENDAR_CHANGES & set(calls):
                    log.warning("reply claims a calendar change without a tool call: %r", reply[:80])
                    if not retried:                     # make it really call the tool (one more round)
                        retried, message = True, NO_FAKE_CHANGES
                        continue
                    reply = "ขอโทษค่ะ เบกกี้ยังแก้ปฏิทินไม่สำเร็จ ลองบอกใหม่อีกครั้งนะคะ"
                elif "ลบ" in reply:                      # Gemini asked "delete X?" itself: that counts as asking
                    self._pending_delete.update(self._events_named_in(reply))
                break
            parts, done = self._run_tools(fcs, pending_before, calls)
            history.append(types.Content(role="user", parts=parts))
            if done is not None:                        # calendar change / delete question: our own reply
                history.append(types.Content(role="model", parts=[types.Part(text=done)]))
                reply = done
                break
            message = parts                             # other tools: Gemini writes the answer
        if reply is None:
            reply = "ขอโทษค่ะ เบกกี้ทำไม่สำเร็จ ลองถามใหม่อีกครั้งนะคะ"
        self._turn_start = len(self.history)
        self.history = history
        return {"reply": reply, "tools_called": calls, "model": model}

    def _events_named_in(self, reply: str) -> dict:
        try:
            return {e["id"]: e for e in self.robot.calendar_context() or [] if e.get("title") and e["title"] in reply}
        except Exception:  # noqa: BLE001
            return {}

    def _run_tools(self, fcs, pending_before: dict, calls: list):
        """Run Gemini's function calls. Returns (function response parts, reply or None).
        A reply is returned when every call was a calendar change (or a delete that needs confirming)."""
        from google.genai import types
        tools = {getattr(f, "__name__", ""): f for f in self.tools}
        parts, said, asks, ok = [], [], [], True
        for fc in fcs:
            name, args = fc.name, dict(fc.args or {})
            calls.append(name)
            if name == "delete_calendar_event" and args.get("event_id") not in pending_before:
                ev = self._event(args.get("event_id"))
                self._pending_delete[args.get("event_id")] = ev
                asks.append(ev)
                result = {"needs_confirmation": True, "event": ev,
                          "note": "not deleted yet: Beggy asked the user to confirm"}
            elif name in tools:
                try:
                    result = tools[name](**args)
                except Exception as e:  # noqa: BLE001 - bad arguments from the model
                    result = {"error": str(e)[:200]}
            else:
                result = {"error": f"unknown tool {name}"}
            if not isinstance(result, dict):
                result = {"result": result}
            parts.append(types.Part.from_function_response(name=name, response=result))
            if name in CALENDAR_CHANGES and "error" not in result and not result.get("needs_confirmation"):
                said.append(describe_change(name, result))
            elif name in CALENDAR_CHANGES and "error" in result and "ask them" not in str(result["error"]):
                log.warning("%s failed: %s", name, str(result["error"])[:200])
                said.append(failed_change(name))      # never let Gemini say it worked
            elif not result.get("needs_confirmation"):
                ok = False                      # another tool, or an error: let Gemini explain
        if not ok:
            return parts, None
        if asks:
            names = " และ ".join(f"{e.get('title', 'นัดนี้')} {when(e)}".strip() for e in asks)
            return parts, " ".join(said + [f"จะให้เบกกี้ลบนัด {names} ใช่ไหมคะ"])
        return parts, " ".join(said) if said else None

    def _event(self, event_id) -> dict:
        try:
            for e in self.robot.calendar_context() or []:
                if e.get("id") == event_id:
                    return e
        except Exception:  # noqa: BLE001
            pass
        return {"id": event_id, "title": "นัดนี้"}

    def set_last_question(self, text: str) -> None:
        """Swap the audio of the last question for its transcript: keeps the history small and readable."""
        from google.genai import types
        i = getattr(self, "_turn_start", None)
        if i is not None and i < len(self.history) and self.history[i].role == "user":
            self.history[i] = types.Content(role="user", parts=[types.Part(text=text)])

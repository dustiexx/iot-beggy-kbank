import json

from beggy.settings import Settings
from beggy.ups import parse


def le(*vals, signed=False):
    return b"".join(v.to_bytes(2, "little", signed=signed) for v in vals)


def test_ups_parse_charging():
    # values captured from the real HAT: 16.697 V, +443 mA, 83 %, charging (0b11100011)
    batt = le(16697) + le(443, signed=True) + le(83, 3938, 65535, 121)
    r = parse(0b11100011, le(8879, 1514, 13850), batt, le(4157, 4163, 4209, 4168))
    assert r["percent"] == 83 and r["voltage_v"] == 16.697 and r["current_ma"] == 443
    assert r["charging"] and r["fast_charge"] and r["plugged_in"] and r["charger"] == "constant_voltage"
    assert r["minutes_to_full"] == 121 and r["minutes_to_empty"] is None
    assert r["min_cell_v"] == 4.157 and r["input_w"] == 13.85


def test_ups_parse_discharging_negative_current():
    batt = le(14800) + le(-950, signed=True) + le(40, 1800, 95, 0)
    r = parse(0b00000000, le(0, 0, 0), batt, le(3700, 3690, 3710, 3700))
    assert r["current_ma"] == -950 and r["minutes_to_empty"] == 95 and r["minutes_to_full"] is None
    assert not r["plugged_in"] and r["min_cell_v"] == 3.69


def test_settings_update_casts_persists_and_ignores_unknown(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    seen = []
    s.on_change(seen.append)
    res = s.update({"posture_bad_after_s": "5", "voice_enabled": "false", "nope": 1, "wake_threshold": "abc"})
    assert res["applied"] == {"posture_bad_after_s": 5.0, "voice_enabled": False}
    assert set(res["ignored"]) == {"nope", "wake_threshold"}
    assert seen == [res["applied"]]
    assert json.loads(path.read_text())["posture_bad_after_s"] == 5.0
    assert Settings(path).get()["voice_enabled"] is False   # survives restart


def test_tts_falls_back_to_offline_espeak(monkeypatch, tmp_path):
    import beggy.tts as tts

    def boom(*a, **k):
        raise ConnectionError("service down")
    monkeypatch.setattr(tts, "_edge", boom)
    monkeypatch.setattr(tts, "_gtts", boom)
    info = tts.Speaker("none", tmp_path).say("สวัสดีค่ะ", "th-TH-PremwadeeNeural")
    assert info["engine"] == "espeak-ng"
    assert info["saved"].endswith(".wav") and (tmp_path / info["saved"].split("/")[-1]).read_bytes()[:4] == b"RIFF"


def _r(pct, plugged=False, cell=3.8):
    return {"percent": pct, "plugged_in": plugged, "charging": plugged, "min_cell_v": cell}


def test_battery_warns_once_at_20_and_once_at_10():
    from beggy.workers import BatteryAlerts
    a = BatteryAlerts()
    seq = [a.update(_r(p)) for p in (50, 25, 20, 19, 18, 12, 10, 9, 5)]
    assert seq == [None, None, 20, None, None, None, 10, None, None]


def test_battery_rearms_after_charging_and_skips_20_when_already_10():
    from beggy.workers import BatteryAlerts
    a = BatteryAlerts()
    assert a.update(_r(20)) == 20
    assert a.update(_r(40, plugged=True)) is None      # plugged in -> re-armed
    assert a.update(_r(19)) == 20                      # warns again next time
    b = BatteryAlerts()
    assert b.update(_r(8)) == 10                       # started already very low: one warning, not two
    assert b.update(_r(7)) is None


def test_battery_low_cell_voltage_counts_as_10_percent():
    from beggy.workers import BatteryAlerts
    a = BatteryAlerts()
    assert a.update(_r(55, cell=3.40)) == 10           # gauge says 55 % but a cell is nearly empty


# --- voice error handling (no real Gemini) ---
import types as _types

import pytest

from beggy import assistant as _assistant
from beggy.workers import VoiceWorker


class _FakeModels:
    def __init__(self, errors):
        self.errors, self.used = list(errors), []

    def generate_content(self, model, **kw):
        self.used.append(model)
        if self.errors:
            raise self.errors.pop(0)
        return _types.SimpleNamespace(text=" สวัสดี ")


def _stt(errors, models=("lite-a", "lite-b", "lite-c")):
    stt = _assistant.Stt.__new__(_assistant.Stt)
    stt.models, stt.last_model, stt.local = list(models), None, None
    stt.client = _types.SimpleNamespace(models=_FakeModels(errors))
    return stt


def test_stt_falls_back_to_the_next_model_when_busy():
    stt = _stt([RuntimeError("503 UNAVAILABLE high demand")])
    assert stt.transcribe(b"wav") == "สวัสดี"
    assert stt.client.models.used == ["lite-a", "lite-b"] and stt.last_model == "lite-b"


def test_stt_gives_up_when_every_model_is_busy():
    stt = _stt([RuntimeError("503 UNAVAILABLE")] * 3)
    with pytest.raises(RuntimeError, match="UNAVAILABLE"):
        stt.transcribe(b"wav")
    assert len(stt.client.models.used) == 3


def test_stt_does_not_retry_other_errors():
    stt = _stt([ValueError("400 bad request")])
    with pytest.raises(ValueError):
        stt.transcribe(b"wav")
    assert stt.client.models.used == ["lite-a"]


def test_thinking_config_matches_the_model_generation():
    assert _assistant._thinking("gemini-2.5-flash-lite").thinking_budget == 0
    assert _assistant._thinking("gemini-3.1-flash-lite").thinking_level is not None


def test_voice_error_is_spoken(tmp_path):
    said = []
    speaker = _types.SimpleNamespace(say=lambda text, voice: said.append(text))
    w = VoiceWorker(None, Settings(tmp_path / "s.json"), lambda *a: None, None, speaker)
    w.say_error(RuntimeError("503 UNAVAILABLE"))
    w.say_error(ValueError("boom"))
    assert "ไม่ว่าง" in said[0] and "ขอโทษ" in said[1]


def test_soft_limit_boosts_without_clipping():
    import numpy as np
    from beggy.tts import soft_limit
    x = np.array([0, 1000, -1000, 20000, -32768], np.int16)
    y = soft_limit(x, 6.0)
    assert abs(int(y[1]) - 1995) <= 2 and abs(int(y[2]) + 1995) <= 2   # +6 dB below the knee
    assert np.abs(y.astype(int)).max() < 32767 and y[3] > 20000 and y[4] < -20000
    assert (soft_limit(x, 0.0)[:3] == x[:3]).all()


def _worker(tmp_path, stt_text=None, stt_error=None, audio_error=None):
    events, said = [], []
    speaker = _types.SimpleNamespace(say=lambda text, voice: said.append(text) or {"engine": "fake"})
    w = VoiceWorker(None, Settings(tmp_path / "s.json"), lambda name, p: events.append(p), None, speaker)

    def transcribe(wav):
        if stt_error:
            raise stt_error
        return stt_text
    w.stt = _types.SimpleNamespace(transcribe=transcribe, last_model="lite-a")
    asked = []

    def ask(text=None, audio=None):
        asked.append("audio" if audio is not None else text)
        if audio is not None and audio_error:
            raise audio_error
        return {"reply": "ฝุ่นน้อยครับ", "tools_called": ["get_environment"], "model": "lite-a"}
    w.assistant = _types.SimpleNamespace(ask=ask, set_last_question=lambda t: asked.append(("history", t)))
    w.asked = asked
    w.DEBUG_DIR = tmp_path / "voice_debug"
    return w, events, said


def test_handle_audio_runs_stt_then_gemini_then_tts(tmp_path):
    w, events, said = _worker(tmp_path, stt_text="ฝุ่นเยอะไหม")
    out = w.handle_audio(b"RIFF...", source="api")
    assert [e["type"] for e in events] == ["transcript", "voice_reply"]
    assert out["transcript"] == "ฝุ่นเยอะไหม" and out["reply"] == "ฝุ่นน้อยครับ" and out["stt_model"] == "lite-a"
    assert said == ["ฝุ่นน้อยครับ"]
    assert w.asked == ["audio", ("history", "ฝุ่นเยอะไหม")]       # one Gemini question, straight from the audio


def test_handle_audio_falls_back_to_the_transcript(tmp_path):
    w, events, said = _worker(tmp_path, stt_text="ฝุ่นเยอะไหม", audio_error=RuntimeError("400 audio"))
    out = w.handle_audio(b"RIFF")
    assert out["type"] == "voice_reply" and w.asked == ["audio", "ฝุ่นเยอะไหม"] and said == ["ฝุ่นน้อยครับ"]


def test_handle_audio_keeps_the_answer_when_only_stt_fails(tmp_path):
    w, events, said = _worker(tmp_path, stt_error=RuntimeError("STT broke"))
    assert w.handle_audio(b"RIFF")["type"] == "voice_reply" and said == ["ฝุ่นน้อยครับ"]


def test_handle_audio_no_speech_and_errors(tmp_path):
    w, events, said = _worker(tmp_path, stt_text="")
    assert w.handle_audio(b"RIFF")["type"] == "no_speech" and said == []
    busy = RuntimeError("503 UNAVAILABLE")
    w, events, said = _worker(tmp_path, stt_error=busy, audio_error=busy)
    assert w.handle_audio(b"RIFF")["type"] == "voice_error" and "ไม่ว่าง" in said[0]
    w, events, said = _worker(tmp_path, stt_error=busy, audio_error=busy)
    w.handle_audio(b"RIFF", speak=False)
    assert said == []


def test_edge_tts_is_paused_after_a_failure(monkeypatch, tmp_path):
    from beggy import tts
    calls = []

    def edge(*a):
        calls.append(1)
        raise RuntimeError("No audio was received")
    monkeypatch.setattr(tts, "_edge", edge)
    monkeypatch.setattr(tts, "_gtts", lambda text, timeout: b"mp3")
    sp = tts.Speaker("none", tmp_path)
    assert sp.synth("ก", "v")[2] == "gtts" and sp.synth("ข", "v")[2] == "gtts"
    assert len(calls) == 1                       # second reply skipped edge-tts
    sp.edge_skip_until = 0                       # pause over -> tried again
    sp.synth("ค", "v")
    assert len(calls) == 2


class _FakeLocal:
    name = "whisper-base"

    def __init__(self):
        self.calls = 0

    def transcribe(self, wav):
        self.calls += 1
        return "ฟุ่นเยอะไหม"


def test_stt_uses_offline_whisper_when_every_gemini_model_is_busy():
    stt = _stt([RuntimeError("503 UNAVAILABLE")] * 3)
    stt.local = _FakeLocal()
    assert stt.transcribe(b"wav") == "ฟุ่นเยอะไหม"
    assert len(stt.client.models.used) == 3 and stt.last_model == "whisper-base"


def test_stt_goes_offline_right_away_without_internet():
    stt = _stt([ConnectionError("[Errno -3] Temporary failure in name resolution")])
    stt.local = _FakeLocal()
    assert stt.transcribe(b"wav") == "ฟุ่นเยอะไหม"
    assert stt.client.models.used == ["lite-a"]           # no point trying the other Gemini models


def test_local_whisper_can_be_turned_off():
    from beggy.local_stt import make_local_stt
    assert make_local_stt("off") is None and make_local_stt("") is None
    assert make_local_stt("base").name == "whisper-base"


def test_gemini_timeouts_respect_api_minimum():
    """Gemini rejects request deadlines under 10 s (400 INVALID_ARGUMENT)."""
    import inspect
    from beggy.assistant import Assistant, Stt
    assert inspect.signature(Stt.transcribe).parameters["timeout_s"].default >= 10
    assert inspect.signature(Assistant.__init__).parameters["timeout_s"].default >= 10


def test_tts_does_not_wait_for_edge_to_fail_before_gtts(monkeypatch, tmp_path):
    import time as _t
    from beggy import tts
    started = []
    monkeypatch.setattr(tts, "_edge", lambda *a: (_t.sleep(0.3), (_ for _ in ()).throw(TimeoutError()))[1])
    monkeypatch.setattr(tts, "_gtts", lambda text, timeout: started.append(_t.monotonic()) or b"mp3")
    sp = tts.Speaker("none", tmp_path, edge_timeout=0.3)
    t0 = _t.monotonic()
    assert sp.synth("ก", "v")[2] == "gtts"
    assert started[0] - t0 < 0.2                 # gTTS started together with edge-tts, not after it failed


def test_tts_prefers_edge_when_it_answers(monkeypatch, tmp_path):
    from beggy import tts
    monkeypatch.setattr(tts, "_edge", lambda *a: b"edge")
    monkeypatch.setattr(tts, "_gtts", lambda text, timeout: b"g")
    assert tts.Speaker("none", tmp_path).synth("ก", "v")[:3] == (b"edge", "mp3", "edge-tts")


def test_is_question_uses_the_female_particles():
    from beggy.workers import is_question
    assert is_question("จะให้นัดเรื่องอะไร เวลาไหนดีคะ") and is_question("Which time?")
    assert not is_question("ห้าทุ่มสิบห้านาทีค่ะ") and not is_question("พักผ่อนได้แล้วนะคะ") and not is_question("")


def test_handle_audio_speaks_without_waiting_for_slow_stt(tmp_path):
    import time as _t
    w, events, said = _worker(tmp_path)
    w.stt = _types.SimpleNamespace(transcribe=lambda wav: (_t.sleep(2.0), "ฝุ่นเยอะไหม")[1], last_model="lite-a")
    t0 = _t.monotonic()
    out = w.handle_audio(b"RIFF")
    assert out["type"] == "voice_reply" and said == ["ฝุ่นน้อยครับ"] and _t.monotonic() - t0 < 1.6
    _t.sleep(1.5)
    assert any(e.get("late") and e["text"] == "ฝุ่นเยอะไหม" for e in events)   # transcript still published


def test_follow_up_detects_questions_ending_in_ka():
    from beggy.workers import is_question
    assert is_question("ได้เลยค่ะ เบกกี้ขอทราบเวลาเริ่มของนัดหมายด้วยนะคะ แล้วเบกกี้จะสร้างนัดให้ค่ะ")


def test_slow_tool_says_please_wait_once_and_only_when_speaking(tmp_path):
    import time as _t
    w, events, said = _worker(tmp_path, stt_text="เมื่อวานฝุ่นเยอะไหม")
    w.speaker = _types.SimpleNamespace(say=lambda text, voice, cache=False: said.append(text) or {"engine": "fake"})

    def ask(text=None, audio=None):
        w.progress("calendar_create")         # calendar: no phrase (the reply comes ~1 s later anyway)
        w.progress("history")
        w.progress("history")                 # second tool in the same question: no second message
        return {"reply": "ฝุ่นน้อยค่ะ", "tools_called": ["get_environment_history"], "model": "lite-a"}
    w.assistant.ask = ask
    w.handle_audio(b"RIFF")
    _t.sleep(0.2)
    assert said.count("รอสักครู่นะคะ เบกกี้กำลังดูข้อมูลย้อนหลังให้ค่ะ") == 1 and "ฝุ่นน้อยค่ะ" in said
    assert [e["kind"] for e in events if e.get("type") == "progress"] == ["history"]
    said.clear()
    w.handle_text("เมื่อวานฝุ่นเยอะไหม", source="api", speak=False)
    _t.sleep(0.2)
    assert said == []                         # API questions without speech stay silent


def test_speaker_caches_fixed_phrases(monkeypatch, tmp_path):
    from beggy import tts
    calls = []
    monkeypatch.setattr(tts, "_edge", lambda *a: calls.append(1) or b"mp3")
    monkeypatch.setattr(tts, "_gtts", lambda text, timeout: b"g")
    sp = tts.Speaker("none", tmp_path)
    sp.prepare("รอสักครู่", "v")
    sp.say("รอสักครู่", "v", cache=True)
    sp.say("รอสักครู่", "v", cache=True)
    assert len(calls) == 1


class _FakeChat:
    """Scripted Gemini chat: each send_message returns the next (text, [(tool, args), ...])."""
    def __init__(self, script, history):
        self.script, self.h, self.sent = script, list(history), []

    def send_message(self, msg):
        from google.genai import types
        self.sent.append(msg)
        text, calls = self.script.pop(0)
        self.h.append(types.Content(role="user", parts=[types.Part(text=str(msg)[:50])]))
        fcs = [_types.SimpleNamespace(name=n, args=a) for n, a in calls]
        parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in calls] or \
            [types.Part(text=text)]
        self.h.append(types.Content(role="model", parts=parts))
        return _types.SimpleNamespace(text=text, function_calls=fcs)

    def get_history(self):
        return self.h


EV = {"id": "e1", "title": "เทส 1", "start": "2026-11-28T13:00:00+07:00", "end": "2026-11-28T14:00:00+07:00",
      "all_day": False}


def _scripted_assistant(script):
    from beggy.assistant import Assistant
    a = Assistant.__new__(Assistant)
    a.models, a.history, a.idle_reset_s, a.last, a._turn_start, a._pending_delete = ["lite-a"], [], 300, 0.0, None, {}
    a.robot = _types.SimpleNamespace(calendar_context=lambda: [EV])
    a.ran, a.chats = [], []

    def create_calendar_event(**kw):
        a.ran.append(("create", kw))
        return {"created": {**EV, "title": kw.get("title", "เทส 1")}}

    def delete_calendar_event(event_id):
        a.ran.append(("delete", event_id))
        return {"deleted": EV}

    def get_environment():
        a.ran.append(("env",))
        return {"dust_pm25": 12}
    a.tools = [create_calendar_event, delete_calendar_event, get_environment]

    def make_chat(model, history=None):
        c = _FakeChat(script, a.history if history is None else history)
        a.chats.append(c)
        return c
    a._make_chat = make_chat
    return a


def test_calendar_change_is_answered_locally_without_a_second_gemini_call():
    a = _scripted_assistant([("", [("create_calendar_event", {"title": "เทส 1"})])])
    r = a.ask("สร้างนัดเทส 1")
    assert a.ran == [("create", {"title": "เทส 1"})] and len(a.chats) == 1          # one Gemini call only
    assert r["reply"].startswith("เบกกี้สร้างนัดเทส 1") and "บ่ายโมง ถึง บ่ายสองโมง" in r["reply"]
    assert [c.role for c in a.history] == ["user", "model", "user", "model"]        # call, result, our reply


def test_other_tools_still_let_gemini_write_the_answer():
    a = _scripted_assistant([("", [("get_environment", {})]), ("ฝุ่นน้อยค่ะ", [])])
    assert a.ask("ฝุ่นเยอะไหม")["reply"] == "ฝุ่นน้อยค่ะ" and len(a.chats) == 2


def test_delete_needs_a_confirmation_turn():
    a = _scripted_assistant([("", [("delete_calendar_event", {"event_id": "e1"})]),
                             ("", [("delete_calendar_event", {"event_id": "e1"})])])
    r = a.ask("ลบนัดเทส 1")
    assert a.ran == [] and "ใช่ไหมคะ" in r["reply"] and "เทส 1" in r["reply"]        # asked, nothing deleted
    r = a.ask("ใช่")
    assert a.ran == [("delete", "e1")] and r["reply"].startswith("เบกกี้ลบนัดเทส 1")


def test_gemini_asking_to_confirm_itself_counts():
    a = _scripted_assistant([("จะให้ลบนัดเทส 1 ใช่ไหมคะ", []),
                             ("", [("delete_calendar_event", {"event_id": "e1"})])])
    a.ask("ลบนัดเทส 1")
    a.ask("ใช่")
    assert a.ran == [("delete", "e1")]                                              # no second "are you sure"


def test_fake_calendar_change_is_retried_then_refused():
    a = _scripted_assistant([("ลบนัดให้แล้วค่ะ", []), ("", [("create_calendar_event", {"title": "เทส 1"})])])
    assert a.ask("สร้างนัด")["tools_called"] == ["create_calendar_event"]
    a = _scripted_assistant([("ลบนัดให้แล้วค่ะ", []), ("ลบนัดให้เรียบร้อยแล้วค่ะ", [])])
    assert "ไม่สำเร็จ" in a.ask("ลบนัด")["reply"]


def test_describe_change_says_the_day():
    import datetime as dt
    from beggy.assistant import when
    assert when(EV, today=dt.date(2026, 11, 27)) == "พรุ่งนี้ บ่ายโมง ถึง บ่ายสองโมง"
    assert when(EV, today=dt.date(2026, 11, 1)) == "วันที่ 28 พฤศจิกายน บ่ายโมง ถึง บ่ายสองโมง"
    assert when({"start": "2026-11-28", "all_day": True}, today=dt.date(2026, 11, 28)) == "วันนี้ ทั้งวัน"


def test_hedged_asks_the_next_model_when_the_first_is_slow():
    import time as _t
    from beggy.assistant import hedged

    def call(model):
        _t.sleep(1.0 if model == "slow" else 0.05)
        return model
    t0 = _t.monotonic()
    assert hedged(["slow", "fast"], call, "t", hedge_after_s=0.2) == ("fast", "fast")
    assert _t.monotonic() - t0 < 0.6


def test_hedged_moves_on_right_away_after_a_busy_error_and_raises_other_errors():
    import pytest
    from beggy.assistant import AllModelsBusy, hedged

    def call(model):
        if model == "a":
            raise RuntimeError("503 UNAVAILABLE")
        return "ok"
    assert hedged(["a", "b"], call, "t", hedge_after_s=5) == ("b", "ok")
    with pytest.raises(AllModelsBusy):
        hedged(["a"], call, "t")
    with pytest.raises(ValueError):
        hedged(["x", "b"], lambda m: (_ for _ in ()).throw(ValueError("400 bad")) if m == "x" else "ok", "t")


def test_a_losing_attempt_cannot_run_tools():
    import time as _t
    from beggy.assistant import guard_tool, hedged
    ran = []
    tool = guard_tool(lambda name: ran.append(name) or {"ok": True})

    def call(model):
        if model == "slow":
            _t.sleep(0.5)
            return tool(name="slow")             # wakes up after "fast" already answered
        return "fast answer"
    assert hedged(["slow", "fast"], call, "t", hedge_after_s=0.1) == ("fast", "fast answer")
    _t.sleep(0.6)
    assert ran == []                             # the late model's calendar change was refused


def test_no_second_model_while_a_tool_is_running():
    import time as _t
    from beggy.assistant import guard_tool, hedged
    started = []
    tool = guard_tool(lambda: _t.sleep(0.4) or "done")

    def call(model):
        started.append(model)
        return tool() if model == "a" else "b answer"
    assert hedged(["a", "b"], call, "t", hedge_after_s=0.1) == ("a", "done")
    assert started == ["a"]


def test_calendar_tools_refuse_to_guess_a_time():
    from beggy.assistant import make_tools
    done = []
    robot = _types.SimpleNamespace(calendar=lambda op, a: done.append(op) or {"created": {}}, progress=lambda k: None)
    tools = {f.__name__: f for f in make_tools(robot)}
    assert "error" in tools["create_calendar_event"](title="x", start_iso="2026-11-27T00:00:00+07:00",
                                                       end_iso="2026-11-27T01:00:00+07:00", time_words="")
    assert "error" in tools["update_calendar_event"](event_id="e1", start_iso="2026-11-27T00:00:00+07:00")
    tools["update_calendar_event"](event_id="e1", title="ชื่อใหม่")          # rename only: no time needed
    tools["create_calendar_event"](title="x", start_iso="2026-11-27T12:00:00+07:00",
                                   end_iso="2026-11-27T14:00:00+07:00", time_words="เที่ยงถึงบ่ายสอง")
    assert done == ["update", "create"]


def test_thai_time_words():
    from beggy.assistant import thai_time
    cases = {(0, 0): "เที่ยงคืน", (1, 0): "ตีหนึ่ง", (6, 0): "หกโมงเช้า", (10, 0): "สิบโมง", (11, 0): "สิบเอ็ดโมง",
             (12, 0): "เที่ยง", (12, 30): "เที่ยงครึ่ง", (13, 0): "บ่ายโมง", (14, 0): "บ่ายสองโมง",
             (16, 0): "สี่โมงเย็น", (18, 0): "หกโมงเย็น", (19, 0): "หนึ่งทุ่ม", (20, 15): "สองทุ่มสิบห้านาที",
             (23, 0): "ห้าทุ่ม", (9, 45): "เก้าโมงเช้าสี่สิบห้านาที", (13, 21): "บ่ายโมงยี่สิบเอ็ดนาที"}
    for (h, m), words in cases.items():
        assert thai_time(h, m) == words


def test_failed_calendar_change_is_never_spoken_as_done():
    a = _scripted_assistant([("", [("create_calendar_event", {"title": "x"})]), ("สร้างนัดให้แล้วค่ะ", [])])
    a.tools[0] = lambda **kw: {"error": "Invalid isoformat string"}
    a.tools[0].__name__ = "create_calendar_event"
    r = a.ask("สร้างนัด")
    assert "ไม่สำเร็จ" in r["reply"] and len(a.chats) == 1


def test_clean_iso_strips_junk():
    from beggy.assistant import clean_iso
    assert clean_iso(" 2026-11-27T12:00:00+07:00, ") == "2026-11-27T12:00:00+07:00"


def test_gtts_is_asked_again_when_slow(monkeypatch):
    import time
    import beggy.tts as tts
    calls = []

    def fake(text, timeout):
        calls.append(1)
        if len(calls) == 1:
            time.sleep(3)
        return b"mp3"
    monkeypatch.setattr(tts, "_gtts", fake)
    sp = tts.Speaker.__new__(tts.Speaker)
    sp.gtts_timeout, sp.edge_timeout, sp.edge_skip_until = 6, 3, float("inf")
    t0 = time.time()
    assert sp.synth("สวัสดี", "v")[0] == b"mp3" and time.time() - t0 < 2.5 and len(calls) == 2


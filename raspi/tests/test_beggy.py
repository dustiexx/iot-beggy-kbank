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
    stt.models, stt.last_model = list(models), None
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


def _worker(tmp_path, stt_text=None, stt_error=None):
    events, said = [], []
    speaker = _types.SimpleNamespace(say=lambda text, voice: said.append(text) or {"engine": "fake"})
    w = VoiceWorker(None, Settings(tmp_path / "s.json"), lambda name, p: events.append(p), None, speaker)

    def transcribe(wav):
        if stt_error:
            raise stt_error
        return stt_text
    w.stt = _types.SimpleNamespace(transcribe=transcribe, last_model="lite-a")
    w.assistant = _types.SimpleNamespace(ask=lambda text: {"reply": "ฝุ่นน้อยครับ", "tools_called": ["get_environment"],
                                                           "model": "lite-a"})
    return w, events, said


def test_handle_audio_runs_stt_then_gemini_then_tts(tmp_path):
    w, events, said = _worker(tmp_path, stt_text="ฝุ่นเยอะไหม")
    out = w.handle_audio(b"RIFF...", source="api")
    assert [e["type"] for e in events] == ["transcript", "voice_reply"]
    assert out["transcript"] == "ฝุ่นเยอะไหม" and out["reply"] == "ฝุ่นน้อยครับ" and out["stt_model"] == "lite-a"
    assert said == ["ฝุ่นน้อยครับ"]


def test_handle_audio_no_speech_and_errors(tmp_path):
    w, events, said = _worker(tmp_path, stt_text="")
    assert w.handle_audio(b"RIFF")["type"] == "no_speech" and said == []
    w, events, said = _worker(tmp_path, stt_error=RuntimeError("503 UNAVAILABLE"))
    assert w.handle_audio(b"RIFF")["type"] == "voice_error" and "ไม่ว่าง" in said[0]
    w, events, said = _worker(tmp_path, stt_error=RuntimeError("503 UNAVAILABLE"))
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

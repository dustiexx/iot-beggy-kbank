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

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

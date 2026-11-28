# Handoff: Dashboard

From the Pi (control board) team. What the Pi sends, so the dashboard can show it, and the settings the dashboard can change.
All messages are JSON (UTF-8). Every message the Pi sends has `type`, `unix_ms` (ms since 1970, Pi clock, NTP-synced) and `device` (`"beggy-pi"`).

**The dashboard doesn't connect to MQTT itself.** It gets realtime data from the backend (SSE / WebSocket) and history from the backend's API (DynamoDB). The messages below are what the backend forwards / stores. ESP32 sensor data: `esp/HANDOFF.md`.

## Suggested pages

| page | data |
|---|---|
| Realtime | latest `posture_status`, `battery_status`, `beggy/esp/env`, `status` (online/offline), upcoming events, last `voice_reply` |
| History | posture good/bad over time, temperature / humidity / light / dust charts, battery |
| Settings | the settings table below |

## What the Pi sends

**`beggy/pi/posture`**

```json
{"type": "posture_status", "state": "bad", "issues": ["slouch"], "raw_issues": ["slouch"],
 "features": {"shoulder_width": 310.2, "neck_ratio": 0.47, "head_drop": 0.08, "shoulder_tilt_deg": -1.2, "head_roll_deg": 2.0},
 "bad_seconds": 14.3, "timestamp": 512.3, "fps": 9.8, "unix_ms": 1795400000000, "device": "beggy-pi"}
```

- `posture_status`: every 5 s (live tile). `posture_state`: only when the state changes (timeline / notifications).
- `state`: `good` | `bad` | `away` (nobody there) | `paused` (head turned away from its posture position, has `reason` instead of the other fields).
- `issues`: why it is bad: `slouch`, `head_down`, `too_close`, `uneven_shoulders`. `raw_issues`: seen in this frame, before the 10 s wait.

**`beggy/pi/battery`**

```json
{"type": "battery_status", "percent": 83, "voltage_v": 16.697, "current_ma": -950, "remaining_mah": 3938,
 "minutes_to_empty": 95, "minutes_to_full": null, "cell_v": [4.157, 4.163, 4.209, 4.168], "min_cell_v": 4.157,
 "charging": false, "fast_charge": false, "plugged_in": false, "charger": "standby",
 "input_v": 0.0, "input_w": 0.0, "unix_ms": 1795400000000, "device": "beggy-pi"}
```

Every 30 s. `current_ma` > 0 charging, < 0 discharging. Also `{"type": "battery_low", "level_pct": 20, "percent": 19, "min_cell_v": 3.61, "minutes_to_empty": 40}` once at 20 % and once at 10 %.

**`beggy/pi/voice`**

| `type` | fields |
|---|---|
| `wake` | `score` (wake word heard) |
| `transcript` | `text` (what the user said), `stt_s` |
| `voice_reply` | `transcript`, `reply` (what Beggy answered), `tools_called` (e.g. `["get_environment"]`), `model`, `think_s`, `source` (`voice` / `api` / `mqtt`), `tts` |
| `no_speech` | woke up but nobody talked |
| `voice_error` | `error` (Gemini / network problem) |

**`beggy/pi/head`**: `{"type": "head_center", "status": "centered" | "no_person" | "timeout" | "at_limit", "pan": 76.0, "tilt": 95.0, "steps": 4, "seconds": 2.1}` and `{"type": "head_pos", "pan": 90, "tilt": 90, "home": true}` when it goes back.

**`beggy/pi/calendar`**: `calendar_upcoming` (`hours`, `events`), `calendar_created` / `calendar_deleted` (one event), `calendar_error` (`reason`: `not_connected` | `login_expired` | `api_error`, `error`). Event: `{"id", "title", "start", "end", "all_day", "location"}`, times ISO 8601 `+07:00`.

**`beggy/pi/status`** (retained): `{"type": "status", "online": true, "uptime_s": 3600, "vision_fps": 9.8}` every 60 s; `{"type": "status", "online": false}` when the Pi stops or loses connection (MQTT last will). Show "Beggy offline" when `online` is false or no status for > 2 min.

## Settings

The dashboard changes Beggy only through settings, sent through the backend (the backend publishes them to MQTT).

**`beggy/pi/settings`**: send only what changes, e.g. `{"posture_bad_after_s": 5, "tts_voice": "th-TH-NiwatNeural"}`. Reply on `beggy/pi/settings_result`: `{"type": "settings_applied", "applied": {...}, "ignored": [...]}`. Settings are kept after a restart.

| setting | default | meaning |
|---|---|---|
| `posture_enabled` | `true` | posture checking on/off |
| `posture_bad_after_s` | `10` | seconds of bad posture before it counts as bad |
| `posture_status_every_s` | `5` | how often `posture_status` is sent |
| `head_auto_home_s` | `30` | after centering, go back to the posture position after this long |
| `voice_enabled` | `true` | voice on/off |
| `wakeword` | `"hey_jarvis"` | or `"models/hey_beggy.joblib"`; applies after the service restarts |
| `wake_threshold` | `0.5` | 0-1, lower = wakes more easily (and more by mistake) |
| `tts_voice` | `"th-TH-PremwadeeNeural"` | female; `"th-TH-NiwatNeural"` male |
| `battery_every_s` | `30` | battery reading interval |
| `battery_low_pct` / `battery_very_low_pct` | `20` / `10` | battery warning levels |

Hardware settings (broker, camera, servo driver, speaker, backend URL) are in `raspi/.env` on the Pi and can't be changed from the dashboard.

## Checklist

- [ ] Realtime page from the backend's live feed
- [ ] History charts from the backend's API
- [ ] Settings page -> backend -> `beggy/pi/settings`, show the reply (`applied` / `ignored`)
- [ ] "Beggy offline" when `online` is false or no status for > 2 min
- [ ] Warning when `calendar_error` has `reason = login_expired`

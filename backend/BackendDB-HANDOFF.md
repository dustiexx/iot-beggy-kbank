# Handoff: Backend / AWS / DynamoDB

From the Pi (control board) team. What the backend must build so the Pi, ESP32 and dashboard work together.
All messages are JSON (UTF-8). Every message the Pi sends has `type`, `unix_ms` (ms since 1970, Pi clock, NTP-synced) and `device` (`"beggy-pi"`).

## All MQTT topics

| topic | publisher | subscriber | purpose |
|---|---|---|---|
| `beggy/esp/env` | ESP32 | Pi, AWS | temperature, humidity, light, dust (the Pi caches the latest values for voice answers) |
| `beggy/pi/posture` | Pi | AWS | posture state changes (`posture_state`) + periodic `posture_status` with fps |
| `beggy/pi/battery` | Pi | AWS | `battery_status` every 30 s, `battery_low` warnings at 20 % / 10 % |
| `beggy/pi/voice` | Pi | AWS | `wake`, `transcript`, `voice_reply` (+ tools Gemini used), `voice_error` |
| `beggy/pi/head` | Pi | AWS | `head_center` results, `head_pos` |
| `beggy/pi/calendar` | Pi | AWS (backend / dashboard) | every 5 min `calendar_upcoming` (next 24 h of events) or `calendar_error` (`reason`: `not_connected` / `login_expired` / `api_error`); `calendar_created` / `calendar_deleted` right away when Gemini adds / removes an event |
| `beggy/pi/status` | Pi | AWS (dashboard) | online/offline (retained, last will) + heartbeat |
| `beggy/pi/settings` | AWS (backend / dashboard) | Pi | runtime settings from the dashboard |
| `beggy/pi/settings_result` | Pi | AWS (backend / dashboard) | which settings were applied / ignored |

The JSON of each Pi message is in `dashboard/HANDOFF.md` > "What the Pi sends" (same messages, the backend stores / forwards them).

### AWS IoT Core for the Pi

1. IoT Core > **Manage > Things > Create thing** `beggy-pi`, auto-generate a certificate, attach the policy below.
2. Send the Pi team: `certificate.pem.crt`, `private.pem.key`, `AmazonRootCA1.pem` (never commit them; they go to `raspi/certs/`) and the **device data endpoint** (IoT Core > Settings, `xxxx-ats.iot.<region>.amazonaws.com`).
3. The Pi switches by editing `.env` (`MQTT_HOST`, `MQTT_PORT=8883`, `MQTT_CA`, `MQTT_CERT`, `MQTT_KEY`); no code change.

Policy for the Pi (replace `REGION` and `ACCOUNT`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "iot:Connect",
     "Resource": "arn:aws:iot:REGION:ACCOUNT:client/beggy-pi"},
    {"Effect": "Allow", "Action": ["iot:Publish", "iot:RetainPublish"],
     "Resource": "arn:aws:iot:REGION:ACCOUNT:topic/beggy/pi/*"},
    {"Effect": "Allow", "Action": "iot:Subscribe",
     "Resource": ["arn:aws:iot:REGION:ACCOUNT:topicfilter/beggy/esp/*",
                  "arn:aws:iot:REGION:ACCOUNT:topicfilter/beggy/pi/settings"]},
    {"Effect": "Allow", "Action": "iot:Receive",
     "Resource": ["arn:aws:iot:REGION:ACCOUNT:topic/beggy/esp/*",
                  "arn:aws:iot:REGION:ACCOUNT:topic/beggy/pi/settings"]}
  ]
}
```

The ESP32 needs its own thing/certificate with `iot:Publish` + `iot:RetainPublish` on `topic/beggy/esp/*` (client id e.g. `esp32-1`).

### Storing everything in DynamoDB (time series)

One IoT Rule can store every message without any code:

```sql
SELECT *, concat(topic(2), '#', topic(3)) AS pk, timestamp() AS ts FROM 'beggy/#'
```

Action: **DynamoDBv2** -> table `beggy_readings`.

| table | partition key | sort key | notes |
|---|---|---|---|
| `beggy_readings` | `pk` (string): `pi#posture`, `pi#battery`, `esp#env`, `pi#calendar`, ... | `ts` (number, ms) | one item per message, all JSON fields as attributes; optional TTL attribute for old raw data |
| `beggy_rollups` (optional) | `pk#hour` / `pk#day` | bucket start (ms) | written by a scheduled Lambda: avg/min/max/count per metric, posture seconds per state |

Read a time range: `pk = :pk AND ts BETWEEN :from AND :to`. This is what the History API (below) and the dashboard's history charts query. Posture seconds = number of `posture_status` items per `state` x 5 s (`posture_status_every_s`); alerts = `posture_state` items with `state = "bad"`.

### Notifications the backend sends

| notification | from | rule |
|---|---|---|
| posture | `beggy/pi/posture`, `type = posture_state`, `state = "bad"` | the Pi already waited `posture_bad_after_s` (10 s) before saying bad, so notify straight away (maybe at most once per N minutes) |
| dust | `beggy/esp/env` `dust_pm25` | the backend picks the threshold, e.g. PM2.5 > 37.5 ug/m3 (Thai standard) |
| calendar deadline | `beggy/pi/calendar`, `type = calendar_upcoming` | event `start` within e.g. 30 min -> notify; **remember notified `id`s**, the same event comes again every 5 min |
| battery | `beggy/pi/battery`, `type = battery_low` | already sent only once at 20 % and once at 10 %, forward as is |
| calendar login | `beggy/pi/calendar`, `type = calendar_error`, `reason = login_expired` | ask someone to run `python -m beggy.gcal login` on the Pi |

### Settings from the dashboard

The dashboard doesn't talk MQTT. When the user changes a setting on the dashboard, the backend publishes it to **`beggy/pi/settings`** and passes the reply from **`beggy/pi/settings_result`** back to the dashboard. The settings list is in `dashboard/HANDOFF.md` > "Settings".

### Realtime to the dashboard

Forward the `beggy/#` messages to the dashboard as they arrive (SSE / WebSocket), and serve history from DynamoDB.

## History API (the Pi calls it for Gemini history questions)

Gemini answers history questions ("how long did I slouch today?", "dust this week?") with the tools `get_posture_history` and `get_environment_history`. They call the backend, which reads DynamoDB; the Pi never holds AWS database credentials. Set `BACKEND_URL` and `BACKEND_KEY` in `.env`; until then the tools answer "history not available yet".

Every request has the header `X-Api-Key: <BACKEND_KEY>`; times are ISO 8601 with offset (`+07:00`).

```
GET /history/posture?from=2026-11-25T00:00:00+07:00&to=2026-11-25T15:30:00+07:00
200 {"good_s": 5400, "bad_s": 1260, "away_s": 900, "alerts": 4,
     "issues_s": {"slouch": 800, "head_down": 460}}

GET /history/env?metric=dust_pm25&from=...&to=...&bucket=hour      (bucket: hour | day)
200 {"metric": "dust_pm25",
     "points": [{"t": "2026-11-25T09:00:00+07:00", "avg": 31.2, "min": 25, "max": 48, "count": 60}]}
```

`metric` is one of `temperature_c`, `humidity_pct`, `light_lux`, `dust_pm25`. Errors: any non-200 status; the Pi tells the user the history is unavailable.

Suggested DynamoDB design: see "Storing everything in DynamoDB" above.

## Checklist

- [ ] IoT Core: thing + certificate + policy for `beggy-pi`, send certs + endpoint to the Pi team
- [ ] IoT Core: thing + certificate for `esp32-1`, send certs + endpoint to the ESP32 person
- [ ] IoT Rule `beggy/#` -> DynamoDB `beggy_readings`
- [ ] Notifications: posture, dust, calendar deadline (+ battery, calendar login)
- [ ] Settings: dashboard -> `beggy/pi/settings`, reply from `beggy/pi/settings_result`
- [ ] Realtime feed + history API for the dashboard
- [ ] History API for the Pi (`/history/posture`, `/history/env`), send `BACKEND_URL` + `BACKEND_KEY` to the Pi team

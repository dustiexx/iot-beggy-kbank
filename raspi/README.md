# raspi: Beggy control board (Raspberry Pi 5)

## Control board server (`beggy_main.py`)

One process runs everything on the Pi and talks to the cloud over MQTT:

| part | what it does |
|---|---|
| vision | camera -> MediaPipe posture on the Pi (edge) + pan/tilt head centering |
| voice | wake word -> STT (Gemini flash-lite) -> thinking with tools (Gemini flash-lite) -> TTS (edge-tts, Thai) -> speaker |
| battery | UPS HAT (E) over I2C: percent, charging, low/critical alerts |
| mqtt | publishes `beggy/pi/*`, receives sensor board data `beggy/esp/+` and settings `beggy/pi/settings` |
| api | HTTP on port 8080: `GET /status`, `GET/POST /settings`, `POST /cmd`, `POST /ask` (local testing on the Pi only; the cloud talks to the Pi through MQTT) |

MQTT topics:

Publisher = sends the message, subscriber = receives it ("AWS" = IoT Rule to DynamoDB + backend).

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

```bash
cp .env.example .env          # Gemini key, broker, camera, drivers
python beggy_main.py
curl localhost:8080/status
curl -X POST localhost:8080/ask -d '{"text": "ตอนนี้ฝุ่นเยอะไหม"}'
curl -X POST localhost:8080/cmd -d '{"action": "center"}'
mosquitto_sub -t 'beggy/#' -v   # watch everything
```

The scripts below (`posture_main.py`, `voice_main.py`, `head_main.py`) are for testing one part on its own.

## Posture detection

Uses Pi Camera Module 3 + MediaPipe Pose (runs on the Pi, no cloud). It watches the nose, eyes, ears and shoulders from the front and compares them with a baseline you record while sitting up straight.

Detected issues:

| issue | meaning |
|---|---|
| `slouch` | ears dropped toward the shoulders (hunched / forward head) |
| `head_down` | looking down (e.g. at a phone) |
| `uneven_shoulders` | leaning to one side |
| `too_close` | leaning in toward the screen |

A problem has to last `bad_after_s` (default 10 s) before the state turns `bad`, and good posture has to last `good_after_s` (3 s) to clear it. All thresholds are in `posture/config.py`.

### Setup on the Pi (Raspberry Pi OS 64-bit)

```bash
sudo apt install -y python3-picamera2 python3-opencv
cd raspi
python3 -m venv --system-site-packages .venv   # system-site-packages so picamera2 is visible
source .venv/bin/activate
pip install mediapipe
./scripts/download_model.sh            # -> models/pose_landmarker_lite.task
rpicam-hello -t 2000                   # check the camera works
```

If `pip install mediapipe` finds no wheel for the Pi's Python version, make the venv with a Python version MediaPipe supports (e.g. via `uv venv --python 3.12 --system-site-packages`).

### Run

```bash
python posture_main.py calibrate                 # sit up straight, look at Beggy for 5 s
python posture_main.py run                       # prints JSON lines
python posture_main.py run --debug-frame /tmp/posture.jpg   # headless: open this jpg to see what it sees
python posture_main.py run --show                # with a monitor / VNC
python posture_main.py run --bad-after 3         # quicker alerts for demos
```

Testing on a laptop with a webcam: `--source 0`. With a recorded clip: `--source clip.mp4`.

Camera mounted upside down: add `--rotate180` to both calibrate and run.

Because the camera is on the moving head, calibrate with the head in the position it will use while checking posture, and move the head back to that position before checking.

### Output (stdout, one JSON per line)

```json
{"type": "posture_state", "state": "bad", "issues": ["slouch"], "raw_issues": ["slouch"],
 "features": {"shoulder_width": 230.4, "neck_ratio": 0.31, "head_drop": 0.04, "shoulder_tilt_deg": 0.0, "head_roll_deg": 0.0},
 "bad_seconds": 10.0, "timestamp": 129.26, "unix_ms": 1795377000000}
```

- `posture_state`: sent when the state changes (`good` / `bad` / `away`). This is what the backend notification should use.
- `posture_status`: sent every `--status-every` seconds (default 5) for the dashboard.

### Tests

```bash
python -m pytest tests
```

## Voice (wake word -> Gemini)

Flow: openWakeWord listens offline for the wake word -> records until you stop talking -> sends the clip to Gemini, which returns the Thai transcript and Beggy's short Thai reply in one call.

The wake word is `hey_jarvis` for now (a ready-made English model) until a custom "hey Beggy" model is trained; swap it with `--wakeword` or in `voice/config.py`.

### Setup

```bash
sudo apt install -y libportaudio2
source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps openwakeword==0.6.0
python -c "import openwakeword.utils as u; u.download_models()"
cp .env.example .env    # then paste your key from https://aistudio.google.com/apikey
```

### Run

```bash
python voice_main.py devices                        # which mic is used (DGM20 by default)
python voice_main.py level                          # loudness meter, check the mic
python voice_main.py listen --dry-run --save-dir clips   # wake word + recording only, no Gemini
python voice_main.py listen                         # say "hey jarvis", then talk in Thai
python voice_main.py listen --no-wake               # press Enter instead of the wake word
```

Output (stdout, one JSON per line):

```json
{"type": "wake", "wakeword": "hey_jarvis", "score": 0.87}
{"type": "recorded", "seconds": 2.4}
{"type": "voice_reply", "transcript": "วันนี้อากาศเป็นยังไง", "reply": "...", "latency_s": 1.8}
```

Other events: `no_speech` (woke but nobody talked), `voice_error` (Gemini/network failure; the loop keeps running).

## Head: center the camera on a person

Pan/tilt servos on the Pi. `center` looks at the MediaPipe pose, aims at the point between the nose and the middle of the shoulders, and nudges the servos toward it until it is within `deadband` of the frame center (or times out / loses the person / hits a limit). Settings: `head/config.py`.

Drivers: `gpio` (servo signal wires on GPIO12 pan / GPIO13 tilt, servos powered from a separate 5 V supply with common ground), `pca9685` (I2C servo board, `pip install adafruit-circuitpython-servokit`), `fake` (prints angles), `sim` (no servos: crops a moving window out of a webcam frame so you can test the loop).

```bash
python head_main.py center --driver sim --source 8 --show   # test without servos
python head_main.py move 120 90      # wiring check: should look left. Wrong way? set pan_invert/tilt_invert
python head_main.py center           # real servos
python head_main.py home             # back to the position used for posture calibration
```

Output: `{"type": "head_center", "status": "centered" | "no_person" | "timeout" | "at_limit", "pan": .., "tilt": ..}`.

Posture readings are only valid at the home position: call `home` before checking posture again after centering.

## Run on boot (systemd)

```bash
./scripts/install_service.sh          # installs + enables: starts on every boot, restarts on crash
sudo systemctl start|stop|restart beggy
journalctl -u beggy -f                # live logs
```

Stop the service before running the single-part test scripts (they need the camera / mic too).

## Battery warnings

iPhone-style: one spoken warning + `battery_low` on `beggy/pi/battery` at 20 %, one more at 10 %, re-armed when the charger is plugged in. No automatic shutdown. Because the HAT's percentage can be off for the first charge cycles, a weakest cell at or below 3.45 V also counts as 10 %. Levels: settings `battery_low_pct`, `battery_very_low_pct`.

## Posture evaluation (fps + accuracy)

```bash
python posture_main.py calibrate --source 8      # sit straight first
python eval_posture.py --source 8 --rounds 2     # do each pose when the window tells you
```

It asks for: good, slouch, head down, too close, leaning to one side, good, leave the view (4 s to get ready, 8 s scored each). Output in `eval_out/<date>/`: `report.md` (speed, accuracy, good-vs-bad precision/recall/false alarms, per-pose recall, confusion matrix), `frames.csv`, and example images in `correct/` and `wrong/`.

## Google Calendar

Gemini can list, create and delete events (it asks before deleting); the next 24 h of events go out on `beggy/pi/calendar` every 5 min for the backend's deadline notifications.

1. [Google Cloud console](https://console.cloud.google.com), the project of the Gemini key: enable **Google Calendar API**
2. **OAuth consent screen** (Google Auth Platform): External, add your Google account under Audience > Test users. Leave the app in **Testing**: then Google ends the login every 7 days, so run step 4 again before a demo (Beggy says the login expired when that happens)
3. **Credentials > Create OAuth client ID > Desktop app**, download the JSON to `raspi/secrets/google_oauth.json`
4. On the Pi's desktop (it opens a browser): `python -m beggy.gcal login` -> `secrets/google_token.json`

`secrets/` is gitignored.

## Custom wake word "hey Beggy"

```bash
python wakeword_train.py synth                    # robot-voice positives + near misses ("hey baby"...)
python wakeword_train.py record-pos --n 40        # you (and teammates) saying "hey Beggy"
python wakeword_train.py record-neg --seconds 180 # normal talking / room noise
python wakeword_train.py train                    # -> models/hey_beggy.joblib, held-out recall + false triggers/hour
python wakeword_train.py live                     # try it
```

Then set the `wakeword` setting to `models/hey_beggy.joblib`. Synthetic data alone is not enough: with it, "hey baby" and "hey becky" still trigger, so real recordings are needed.

## History API (Gemini history questions)

`beggy/history.py` asks the backend for past posture / sensor data (`BACKEND_URL`, `BACKEND_KEY` in `.env`). The API contract is at the top of that file. Until the backend is connected, Gemini answers that history isn't available yet.

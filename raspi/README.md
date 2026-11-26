# raspi: Beggy control board (Raspberry Pi 5)

## Control board server (`beggy_main.py`)

One process runs everything on the Pi and talks to the cloud over MQTT:

| part | what it does |
|---|---|
| vision | camera -> MediaPipe posture on the Pi (edge) + pan/tilt head centering |
| voice | wake word -> Gemini flash-lite answers straight from the recorded audio (with tools), while STT runs in parallel only for the transcript (logs / MQTT; the reply is not held back for it) -> TTS (edge-tts and gTTS race; edge-tts if it is ready within 3 s) -> speaker. The last 1 s before the wake is kept so a question said right after "hey Beggy" isn't cut; when Beggy asks something back it listens 6 s for the answer without the wake word. The next 7 days of calendar events are in Gemini's instructions (cached), so calendar questions skip a list round trip; the last 20 recordings are kept in `voice_debug/` (gitignored). If a model answers 503 "high demand" (common on the free tier), the next lite model takes over: 3.1 → 3.5 → 2.5 flash-lite (`GEMINI_MODEL` / `GEMINI_STT_MODEL`, comma lists). If they all fail (or there is no internet), STT falls back to offline Whisper on the Pi (`WHISPER_MODEL=base`, faster-whisper, ~2.4 s, small tone slips like "ฟุ่น" for "ฝุ่น"); if the answer step fails too, Beggy says so out loud. edge-tts often refuses for a while: after one failure it is skipped for 10 min (gTTS instead) |
| battery | UPS HAT (E) over I2C: percent, charging, low/critical alerts |
| mqtt | publishes `beggy/pi/*`, receives sensor board data `beggy/esp/+` and settings `beggy/pi/settings` |
| api | HTTP on port 8080: `GET /status`, `GET/POST /settings`, `POST /cmd`, `POST /ask`, `POST /ask_audio` (local testing on the Pi only; the cloud talks to the Pi through MQTT) |

MQTT topics:

Publisher = sends the message, subscriber = receives it ("AWS" = IoT Rule to DynamoDB + backend).

| topic | publisher | subscriber | purpose |
|---|---|---|---|
| `beggy/esp/env` | ESP32 | Pi, AWS | temperature, humidity, light, dust (the Pi caches the latest values for voice answers) |
| `beggy/pi/posture` | Pi | AWS | posture state changes (`posture_state`) + periodic `posture_status` with fps |
| `beggy/pi/battery` | Pi | AWS | `battery_status` every 30 s, `battery_low` warnings at 20 % / 10 % |
| `beggy/pi/voice` | Pi | AWS | `wake`, `transcript`, `voice_reply` (+ tools Gemini used), `voice_error` |
| `beggy/pi/head` | Pi | AWS | `head_center` results, `head_pos` |
| `beggy/pi/calendar` | Pi | AWS (backend / dashboard) | every 5 min `calendar_upcoming` (next 24 h of events) or `calendar_error` (`reason`: `not_connected` / `login_expired` / `api_error`); `calendar_created` / `calendar_updated` / `calendar_deleted` right away when Gemini adds / moves or renames / removes an event |
| `beggy/pi/status` | Pi | AWS (dashboard) | online/offline (retained if `MQTT_RETAIN`, last will) + heartbeat |
| `beggy/pi/settings` | AWS (backend / dashboard) | Pi | runtime settings from the dashboard |
| `beggy/pi/settings_result` | Pi | AWS (backend / dashboard) | which settings were applied / ignored |

```bash
cp .env.example .env          # Gemini key, broker, camera, drivers
python beggy_main.py
curl localhost:8080/status
curl -X POST localhost:8080/ask -d '{"text": "ตอนนี้ฝุ่นเยอะไหม"}'
curl -X POST localhost:8080/ask_audio --data-binary @question.wav   # 16 kHz mono wav, same path as the mic (?speak=0 = no TTS)
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

The service uses the custom "hey Beggy" model by default (`models/hey_beggy.joblib`, in git, threshold 0.98; defaults in `beggy/settings.py`, override in `settings.json` / the dashboard; changing the wake word needs a service restart, the threshold does not). `hey_jarvis` (ready-made, threshold 0.4) is the fallback.

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

Drivers: `hwpwm` (Pi 5 hardware PWM through `/sys/class/pwm`: steady pulses, no jitter; the one we use), `gpio` (gpiozero software PWM on the same pins: works but the servos jitter), `pca9685` (I2C servo board, `pip install adafruit-circuitpython-servokit`), `fake` (prints angles), `sim` (no servos: crops a moving window out of a webcam frame so you can test the loop).

**Our servos:** pan is a **360° continuous-rotation SG90** (the pulse sets speed, not angle), tilt is a normal 180° SG90. With `pan_continuous = True` (`head/config.py`) a pan move is a timed spin: `|delta| / pan_deg_s_pos|neg` seconds, then the pulses stop (which stops the servo). The pan angle is only an **estimate**, saved in `head_state.json` so `head_main.py` and `beggy_main.py` share it, and `pan_min`/`pan_max` apply to that estimate so it can't wind up the cables. It drifts over time: line the head up by eye and run `pan-zero` to reset it. Measured speeds (2026-11-26): about 60°/s toward + (1.55 ms) and 120°/s toward − (1.35 ms).

```bash
python head_main.py center --driver sim --source 8 --show   # test without servos
python head_main.py tilt 100 --hold 3   # move ONE servo only (the other stays off); --raw ignores the limits
python head_main.py pan 110             # 360 pan: timed spin from the estimated angle
python head_main.py spin 3 --dir 1      # 360 pan: spin 3 s to measure deg/s (mark the horn, count the turn)
python head_main.py pan-zero            # 360 pan: "the head is at home_pan now"
python head_main.py move 120 90         # both servos. Wrong way when centering? set pan_invert/tilt_invert
python head_main.py center              # real servos
python head_main.py home                # back to the position used for posture calibration
```

Output: `{"type": "head_center", "status": "centered" | "no_person" | "timeout" | "at_limit", "pan": .., "tilt": ..}`.

Posture readings are only valid at the home position: call `home` before checking posture again after centering.

## Wiring (speaker + servos)

| Part | Pi pin |
|---|---|
| MAX98357A amp | BCLK GPIO18, LRC GPIO19, DIN GPIO21, VIN 5 V (pin 2), GND. SD_MODE not connected (amp always on) |
| SG90 pan (360°) | signal GPIO12 |
| SG90 tilt (180°) | signal GPIO13 |

The servos are powered from the Pi's **5 V header** (no separate supply), so: no capacitor yet → add 470–1000 µF across the servo 5 V/GND, move one servo at a time, keep moves slow (`slew_deg_s`), and check `vcgencmd get_throttled` (must stay `0x0`). In the bring-up tests (servos and speaker tested separately, never at the same time) it stayed `0x0`.

`/boot/firmware/config.txt` (then reboot):

```
dtoverlay=max98357a,no-sdmode
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```

`.env`: `AUDIO_OUT=plughw:CARD=MAX98357A,DEV=0`, `HEAD_DRIVER=hwpwm`. Test the speaker: `aplay -D plughw:CARD=MAX98357A,DEV=0 some.wav`.

## AWS IoT Core

`.env`: `MQTT_HOST=<id>-ats.iot.ap-southeast-1.amazonaws.com`, `MQTT_PORT=8883`, `MQTT_CA/CERT/KEY` = files in `certs/` (gitignored). If the thing's policy has no `iot:RetainPublish`, AWS **drops the connection** on any retained publish, including the last will: set `MQTT_RETAIN=false` (status is then not retained). Watch messages in the AWS console: IoT Core → MQTT test client → subscribe `beggy/#`.

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

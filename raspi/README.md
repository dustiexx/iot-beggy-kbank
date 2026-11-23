# raspi: Beggy control board (Raspberry Pi 5)

## Control board server (`beggy_main.py`)

One process runs everything on the Pi and talks to the cloud over MQTT:

| part | what it does |
|---|---|
| vision | camera -> MediaPipe posture on the Pi (edge) + pan/tilt head centering |
| voice | wake word -> STT (Gemini flash-lite) -> thinking with tools (Gemini flash) -> TTS (edge-tts, Thai) -> speaker |
| battery | UPS HAT (E) over I2C: percent, charging, low/critical alerts |
| mqtt | publishes `beggy/pi/*`, receives sensor board data `beggy/esp/+`, commands and settings |
| api | HTTP on port 8080: `GET /status`, `GET/POST /settings`, `POST /cmd`, `POST /ask` |

MQTT topics:

| topic | direction | purpose |
|---|---|---|
| `beggy/esp/+` | sensor board -> | temperature, humidity, light, dust (the Pi caches the latest values for voice answers) |
| `beggy/pi/posture` | Pi -> | posture state changes (`posture_state`) + periodic `posture_status` with fps |
| `beggy/pi/battery` | Pi -> | `battery_status` every 30 s, `battery_low` / `battery_critical` alerts |
| `beggy/pi/voice` | Pi -> | `wake`, `transcript`, `voice_reply` (+ tools Gemini used), `voice_error` |
| `beggy/pi/head` | Pi -> | `head_center` results, `head_pos` |
| `beggy/pi/status` | Pi -> | online/offline (retained, last will) + heartbeat |
| `beggy/pi/cmd` | -> Pi | `{"action": "center" / "home" / "say" / "ask", "text": ...}`; reply on `beggy/pi/cmd_result` |
| `beggy/pi/settings` | -> Pi | runtime settings from the dashboard; reply on `beggy/pi/settings_result` |

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

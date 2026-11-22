# raspi: Beggy control board (Raspberry Pi 5)

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

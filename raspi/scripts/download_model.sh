#!/usr/bin/env bash
# Download the MediaPipe pose model. "lite" is fastest on the Pi 5; use "full" if accuracy is poor.
set -euo pipefail
VARIANT="${1:-lite}"   # lite | full | heavy
cd "$(dirname "$0")/.."
mkdir -p models
URL="https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_${VARIANT}/float16/latest/pose_landmarker_${VARIANT}.task"
curl -fL -o "models/pose_landmarker_${VARIANT}.task" "$URL"
echo "saved models/pose_landmarker_${VARIANT}.task"

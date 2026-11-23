"""Wake word detectors with one interface: predict(chunk_16k_int16) -> score 0..1, reset().

- built-in openWakeWord models by name ("hey_jarvis", "alexa", ...)
- our own "hey Beggy" model trained with wakeword_train.py (a .joblib file): a small sklearn
  classifier on top of openWakeWord's speech embeddings (16 frames x 96 = the last ~1.3 s)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


class BuiltinWake:
    def __init__(self, name: str):
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[name], inference_framework="onnx")

    def predict(self, chunk: np.ndarray) -> float:
        return float(max(self.model.predict(chunk).values()))

    def reset(self):
        self.model.reset()


class CustomWake:
    """Streaming: openWakeWord's AudioFeatures keeps the melspectrogram/embedding buffers;
    every 80 ms chunk adds one embedding frame and we score the last 16."""

    def __init__(self, path: str | Path):
        import joblib
        from openwakeword.utils import AudioFeatures
        bundle = joblib.load(path)
        self.clf = bundle["model"]
        self.frames = bundle.get("frames", 16)
        self.features = AudioFeatures(inference_framework="onnx")
        self.warm = 0

    def predict(self, chunk: np.ndarray) -> float:
        self.features(chunk)
        self.warm += 1
        if self.warm < self.frames + 2:      # buffers not full yet after start / reset
            return 0.0
        x = self.features.get_features(self.frames).reshape(1, -1)
        return float(self.clf.predict_proba(x)[0, 1])

    def reset(self):
        self.features.reset()
        self.warm = 0


def make_wake(name_or_path: str):
    if name_or_path.endswith(".joblib"):
        return CustomWake(name_or_path)
    return BuiltinWake(name_or_path)

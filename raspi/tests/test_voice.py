import io
import wave

import numpy as np

from voice.audio import Endpointer, rms, to_16k, to_wav


def make_ep(**kw):
    args = dict(noise_floor=100.0, chunk_s=0.08, start_timeout_s=1.0, end_silence_s=0.4,
                max_record_s=5.0, speech_factor=3.0, min_speech_rms=300.0)
    args.update(kw)
    return Endpointer(**args)


def test_endpointer_times_out_without_speech():
    ep = make_ep()
    states = [ep.push(50.0) for _ in range(13)]
    assert states[-1] == "timeout"
    assert set(states[:-1]) == {"waiting"}


def test_endpointer_ends_after_silence():
    ep = make_ep()
    assert ep.push(50.0) == "waiting"
    assert ep.push(2000.0) == "speaking"
    for _ in range(5):
        assert ep.push(2000.0) == "speaking"
    states = [ep.push(50.0) for _ in range(5)]
    assert states[-1] == "done"
    assert "done" not in states[:-1]


def test_endpointer_max_length():
    ep = make_ep(max_record_s=0.5)
    states = [ep.push(2000.0) for _ in range(7)]
    assert states[-1] == "done"


def test_threshold_uses_noise_floor_when_loud_room():
    assert make_ep(noise_floor=500.0).threshold == 1500.0
    assert make_ep(noise_floor=10.0).threshold == 300.0


def test_resample_48k_to_16k_keeps_length_and_tone():
    t = np.arange(3840) / 48000
    x = (10000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    y = to_16k(x, 3)
    assert y.dtype == np.int16 and len(y) == 1280
    assert abs(rms(y) - rms(x)) / rms(x) < 0.05


def test_to_wav_header():
    wav = to_wav(np.zeros(1600, dtype=np.int16), 16000)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (16000, 1, 2, 1600)


def test_custom_wake_stream_interface(tmp_path):
    """CustomWake must score streaming 80 ms chunks with a saved sklearn model."""
    import joblib
    from sklearn.dummy import DummyClassifier

    from voice.wake import make_wake
    clf = DummyClassifier(strategy="prior").fit(np.zeros((4, 16 * 96)), [0, 0, 0, 1])
    path = tmp_path / "w.joblib"
    joblib.dump({"model": clf, "frames": 16}, path)
    w = make_wake(str(path))
    scores = [w.predict(np.zeros(1280, np.int16)) for _ in range(25)]
    assert scores[0] == 0.0                     # warming up
    assert abs(scores[-1] - 0.25) < 1e-6        # then the model's probability
    w.reset()
    assert w.predict(np.zeros(1280, np.int16)) == 0.0


def test_mic_read_times_out_instead_of_hanging():
    import queue
    import pytest
    from voice.audio import Mic, MicStalled
    mic = Mic.__new__(Mic)                    # no real device
    mic.q, mic.factor = queue.Queue(), 1
    with pytest.raises(MicStalled):
        mic.read(timeout=0.05)

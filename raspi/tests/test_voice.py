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

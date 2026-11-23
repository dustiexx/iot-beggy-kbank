"""Tunable settings for the voice pipeline: wake word -> record -> Gemini."""
from dataclasses import dataclass, field

SYSTEM_PROMPT = """\
You are Beggy, a small friendly desk robot that helps the user sit well and stay focused.
The user talks to you in Thai (sometimes mixed with English words).
You receive a short audio clip of what the user said.
1. Write exactly what they said in `transcript` (Thai script, keep English words as spoken).
2. Write your answer in `reply`: Thai, friendly, at most 2 short sentences, because it will be spoken aloud.
If the clip has no clear speech, set `transcript` to an empty string and `reply` to a short Thai request to repeat.
"""


@dataclass
class VoiceConfig:
    # --- microphone ---
    device: str = "DGM20"            # part of the input device name (see `voice_main.py devices`)
    mic_rate: int = 48000            # what the USB mic supports; resampled to 16 kHz in code
    rate: int = 16000                # what the wake word model and Gemini get
    chunk: int = 1280                # 80 ms at 16 kHz (openWakeWord frame size)

    # --- wake word (openWakeWord) ---
    wakeword: str = "hey_jarvis"     # placeholder until a custom "hey beggy" model is trained
    wake_threshold: float = 0.5
    wake_cooldown_s: float = 2.0     # ignore new wake detections right after one fired

    # --- recording after the wake word ---
    start_timeout_s: float = 5.0     # give up if nobody starts talking within this time
    end_silence_s: float = 1.0       # this much silence after speech ends the recording
    max_record_s: float = 12.0
    speech_factor: float = 3.0       # speech = RMS above noise floor * this
    min_speech_rms: float = 300.0    # ...and at least this (int16 units)

    # --- Gemini ---
    model: str = "gemini-3.8-flash"  # override with GEMINI_MODEL in .env
    system_prompt: str = field(default=SYSTEM_PROMPT)

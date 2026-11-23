"""Send a recorded clip to Gemini; get back the Thai transcript and Beggy's reply."""
from __future__ import annotations

import os

from pydantic import BaseModel


class VoiceReply(BaseModel):
    transcript: str
    reply: str


class GeminiVoice:
    def __init__(self, model: str, system_prompt: str, api_key: str | None = None):
        from google import genai
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set - put it in raspi/.env (see .env.example)")
        self.client = genai.Client(api_key=key)
        self.model = os.environ.get("GEMINI_MODEL", model)
        self.system_prompt = system_prompt

    def ask(self, wav: bytes) -> VoiceReply:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=wav, mime_type="audio/wav")],
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                response_mime_type="application/json",
                response_schema=VoiceReply,
            ),
        )
        if resp.parsed is not None:
            return resp.parsed
        return VoiceReply.model_validate_json(resp.text)

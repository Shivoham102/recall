import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
_TTS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"


def synthesize(text: str) -> str:
    """Call ElevenLabs and return base64-encoded MP3."""
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    resp = requests.post(_TTS_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")

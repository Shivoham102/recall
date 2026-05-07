import os
from cartesia import Cartesia

_client: Cartesia | None = None


def _get_client() -> Cartesia:
    global _client
    if _client is None:
        _client = Cartesia(api_key=os.environ["CARTESIA_API_KEY"])
    return _client


def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    suffix = ".webm" if "webm" in mime_type else ".wav"
    resp = _get_client().stt.transcribe(
        file=(f"audio{suffix}", audio_bytes, mime_type),
        model="ink-whisper",
    )
    return resp.text

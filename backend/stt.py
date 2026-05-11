import os
from cartesia import Cartesia

_client: Cartesia | None = None


def _get_client() -> Cartesia:
    global _client
    if _client is None:
        _client = Cartesia(api_key=os.environ["CARTESIA_API_KEY"])
    return _client


def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    clean_mime = mime_type.split(";")[0].strip()
    suffix = ".webm" if "webm" in clean_mime else ".wav"
    if not audio_bytes:
        raise ValueError("Empty audio received — recording may have been too short")
    resp = _get_client().stt.transcribe(
        file=(f"audio{suffix}", audio_bytes, clean_mime),
        model="ink-whisper",
    )
    return resp.text

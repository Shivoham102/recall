import base64
import os
from cartesia import AsyncCartesia

_client: AsyncCartesia | None = None


def _get_client() -> AsyncCartesia:
    global _client
    if _client is None:
        _client = AsyncCartesia(api_key=os.environ["CARTESIA_API_KEY"])
    return _client


async def synthesize(text: str) -> str:
    voice_id = os.environ["CARTESIA_VOICE_ID"]
    audio_iter = await _get_client().tts.bytes(
        model_id="sonic-2",
        transcript=text,
        voice={"mode": "id", "id": voice_id},
        output_format={
            "container": "mp3",
            "bit_rate": 128,
            "sample_rate": 44100,
        },
    )
    chunks = [chunk async for chunk in audio_iter]
    return base64.b64encode(b"".join(chunks)).decode()

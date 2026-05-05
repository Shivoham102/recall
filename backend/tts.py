import base64
import os

from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None
VOICE = os.environ.get("OPENAI_TTS_VOICE", "nova")


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


async def synthesize(text: str) -> str:
    resp = await _get_client().audio.speech.create(
        model="tts-1",
        voice=VOICE,
        input=text,
    )
    return base64.b64encode(resp.content).decode()

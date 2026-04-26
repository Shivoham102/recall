import base64
import os

from openai import AsyncOpenAI

_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
VOICE = os.environ.get("OPENAI_TTS_VOICE", "nova")


async def synthesize(text: str) -> str:
    resp = await _client.audio.speech.create(
        model="tts-1",
        voice=VOICE,
        input=text,
    )
    return base64.b64encode(resp.content).decode()

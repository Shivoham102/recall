"""LLM-backed short chat titles from the first user message only."""
import os
import re
from anthropic.types import TextBlock

from agent import async_client

# Default: fast Haiku 4.5 (3.5 Haiku snapshot IDs are deprecated for new accounts).
_TITLE_MODEL = os.environ.get("RECALL_TITLE_MODEL", "claude-haiku-4-5-20251001")

_SYSTEM = """You name a chat thread from the user's first message only (like ChatGPT sidebar titles).
Rules:
- Output ONLY the title text. No quotes. No explanation. No trailing punctuation.
- 2–6 words. Capture the main topic or intent, not the whole sentence.
- Do not paste long phrases from the message; distill to a label."""


def clean_title(raw: str) -> str | None:
    t = raw.strip().strip("\"'`")
    t = re.split(r"[\n\r]+", t)[0].strip()
    t = re.sub(r"[.!?,;:]+$", "", t).strip()
    if not t:
        return None
    if len(t) > 72:
        t = t[:69].rstrip() + "…"
    return t


def _trim_user_text(text: str, max_len: int = 4000) -> str:
    t = text.replace("\n", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


async def suggest_title_from_first_message(user_text: str) -> str | None:
    text = user_text.strip()
    if not text or text == "...":
        return None
    trimmed = _trim_user_text(text)
    try:
        msg = await async_client.messages.create(
            model=_TITLE_MODEL,
            max_tokens=48,
            temperature=0.35,
            system=_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"First user message:\n{trimmed}\n\nSuggested chat title:",
                }
            ],
        )
    except Exception:
        return None
    out = ""
    for block in msg.content:
        if isinstance(block, TextBlock):
            out += block.text
    return clean_title(out)

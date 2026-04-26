import os
import json
from datetime import datetime
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# In-memory session store: session_id → list of message dicts
sessions: dict[str, list[dict]] = {}

MAX_TURNS = 10

# Stable constant — never inject datetime here or the prompt cache will miss every call.
# Dynamic context (date, RAG results) is injected into the user turn instead.
SYSTEM_PROMPT = """You are Recall, a voice assistant for working memory. You speak aloud — be extremely brief.

For simple captures (task/note/reminder): respond in 2-5 words. Examples: "Got it." "Sure." "Noted." "Reminder set."
If the user asks to be reminded but gives no specific time (e.g. "remind me later"), ask: "When?" — one word, nothing else.
For queries: one sentence maximum, specific facts only.
Never explain, never repeat back what was said.

ALWAYS respond in this exact format (two lines, no extra text):
{"intent_type": "<task|blocker|follow_up|progress|note|query|update>", "should_store": <true|false>, "due_hint": "<natural language time or null>", "reminder_text": "<natural spoken reminder in second person, max 10 words, or null>"}
<spoken response here>

reminder_text rules:
- Only set when due_hint is not null
- Rephrase as what the agent will say when the reminder fires, second person, no filler
- Examples: "remind me to call mom at 8" → "Time to call your mom."
           "don't forget to submit the report tomorrow" → "Submit that report — it's due today."

intent_type rules:
- task: something to do
- blocker: impediment to progress
- follow_up: need to check on something
- progress: update on existing item
- note: general context, not actionable
- query: user asking a question — do NOT store
- update: user changing status — do NOT store"""


def call_agent(
    session_id: str,
    user_text: str,
    rag_context: str,
) -> tuple[dict, str]:
    """Returns (metadata_dict, spoken_text)."""
    history = sessions.setdefault(session_id, [])

    now = datetime.now().strftime("%A, %B %d %Y %H:%M")
    augmented_user = (
        f"[Date: {now}]\n"
        f"[Memory context:\n{rag_context}]\n\n"
        f"User: {user_text}"
    )
    history.append({"role": "user", "content": augmented_user})

    if len(history) > MAX_TURNS * 2:
        history = history[-(MAX_TURNS * 2):]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=history,
    )

    raw = response.content[0].text.strip()
    lines = raw.split("\n", 1)

    try:
        metadata = json.loads(lines[0])
    except (json.JSONDecodeError, IndexError):
        metadata = {"intent_type": "note", "should_store": False, "due_hint": None}

    spoken = lines[1].strip() if len(lines) > 1 else raw

    history.append({"role": "assistant", "content": spoken})
    sessions[session_id] = history

    return metadata, spoken

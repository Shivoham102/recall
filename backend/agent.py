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
SYSTEM_PROMPT = """You are Recall, a concise voice assistant for managing working memory.
Keep all responses under 2 sentences — this will be spoken aloud.

On CAPTURE: Classify the item. If ambiguous, ask ONE clarifying question.
On QUERY: Answer from the retrieved context. Be specific about statuses and dates.

ALWAYS respond in this exact format (two lines, no extra text):
{"intent_type": "<task|blocker|follow_up|progress|note|query|update>", "should_store": <true|false>, "due_hint": "<text or null>"}
<spoken response here>

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

import os
import json
import asyncio
from datetime import datetime
from typing import AsyncGenerator
from anthropic import Anthropic, AsyncAnthropic
from anthropic.types import TextBlock, ToolUseBlock
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
async_client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Simple session store for the legacy /capture endpoint
sessions: dict[str, list[dict]] = {}

# Extended session store for the agentic /capture/stream endpoint
# Stores full content block lists so tool_use turns are preserved in history
agentic_sessions: dict[str, list[dict]] = {}

MAX_TURNS = 10
MAX_AGENT_ITERATIONS = 8

# ── Stable system prompt blocks (never inject datetime here — cache will miss) ──

SYSTEM_PROMPT_IDENTITY = """You are Recall, a voice assistant for working memory running on Windows 11.
You speak aloud — be extremely brief. Never explain, never repeat back what was said.
For simple captures (task/note/reminder): respond in 2-5 words. Examples: "Got it." "Sure." "Noted."
If the user asks to be reminded but gives no specific time, ask: "When?" — one word, nothing else.
For queries: one sentence maximum, specific facts only.
Never fabricate results — call the appropriate tool, then respond based on what it returns."""

SYSTEM_PROMPT_TOOL_RULES = """Tool usage rules:
- Call classify_intent on every turn where you give a spoken response without doing agentic work.
- For recall_update_item, calendar_create: speak the proposed action first (do NOT call the tool yet). Wait for the user to confirm next turn, then execute.
- gmail_find_contact, gmail_fetch_style_samples, recall_search, calendar_list, and file_create are always safe — call them immediately.
- gmail_draft is safe to call immediately once you have the recipient and body ready.
- You can ONLY draft emails, never send them. If the user asks to send an email, draft it and tell them it has been saved as a draft.
- Email drafting sequence: (1) if you don't have the recipient's email address, call gmail_find_contact first; (2) always call gmail_fetch_style_samples before writing the body; (3) write the body matching the user's tone, sentence length, and vocabulary from those samples; (4) call gmail_draft. Never skip the style samples step — the email must sound like the user, not generic AI.
- recall_search and calendar_list are read-only — call them immediately to gather context.
- Only call one tool per turn unless parallel information is clearly required.
- When the user asks for an update, briefing, or "what's new": call gmail_get_updates. If they say "since last time" or "since we checked in", set since_last_checkin=true. Read the indexed results, decide which emails (typically 3-5 most relevant) to discuss, call surface_cards with only those indices, then give one short spoken sentence per email — lead with the sender's first name and the gist. Example: "Sarah wants to reschedule Thursday. Mike sent over the contract. That's it." Never read out subjects verbatim — summarize. If there's nothing new, say so in one sentence.
- IMPORTANT: Whenever you are about to use any tool, ALWAYS include a brief spoken acknowledgment (2-5 words) as a text block in the SAME response as your tool calls. Examples: "Sure, on it." "Let me check." "Finding his email." "On it." This text is played to the user immediately while the tools run — without it the user hears silence and thinks nothing is happening."""

SYSTEM_PROMPT_BLOCKS = [
    {"type": "text", "text": SYSTEM_PROMPT_IDENTITY, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": SYSTEM_PROMPT_TOOL_RULES, "cache_control": {"type": "ephemeral"}},
]

# Legacy single-block prompt kept for call_agent() backward compat
_LEGACY_SYSTEM_PROMPT = """You are Recall, a voice assistant for working memory. You speak aloud — be extremely brief.

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


def _augment_user_turn(user_text: str, rag_context: str) -> str:
    now = datetime.now().strftime("%A, %B %d %Y %H:%M")
    return (
        f"[Date: {now}]\n"
        f"[Memory context:\n{rag_context}]\n\n"
        f"User: {user_text}"
    )


# ── Legacy synchronous agent (used by /capture and /query endpoints) ──────────

def call_agent(
    session_id: str,
    user_text: str,
    rag_context: str,
) -> tuple[dict, str]:
    """Returns (metadata_dict, spoken_text). Preserved for backward compat."""
    history = sessions.setdefault(session_id, [])

    augmented_user = _augment_user_turn(user_text, rag_context)
    history.append({"role": "user", "content": augmented_user})

    if len(history) > MAX_TURNS * 2:
        history = history[-(MAX_TURNS * 2):]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[{"type": "text", "text": _LEGACY_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
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


# ── Agentic loop (used by /capture/stream endpoint) ───────────────────────────

async def run_agentic_loop(
    session_id: str,
    user_text: str,
    rag_context: str,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that yields SSE event dicts.
    Runs a tool_use loop until stop_reason == 'end_turn' or max iterations reached.
    """
    # Import here to avoid circular imports at module load time
    from tools import TOOL_DEFINITIONS, TOOL_REGISTRY

    history = agentic_sessions.setdefault(session_id, [])

    augmented_user = _augment_user_turn(user_text, rag_context)
    history.append({"role": "user", "content": augmented_user})

    if len(history) > MAX_TURNS * 2:
        history = history[-(MAX_TURNS * 2):]

    metadata = {"intent_type": "note", "should_store": False, "due_hint": None, "reminder_text": None}
    spoken = ""

    for iteration in range(MAX_AGENT_ITERATIONS):
        yield {"type": "thinking", "text": "Thinking..."}

        response = await async_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT_BLOCKS,
            tools=TOOL_DEFINITIONS,
            tool_choice={"type": "auto"},
            messages=history,
        )

        # Extract any text and classify_intent calls from this response
        tool_use_blocks: list[ToolUseBlock] = []
        for block in response.content:
            if isinstance(block, TextBlock) and block.text.strip():
                spoken = block.text.strip()
            elif isinstance(block, ToolUseBlock):
                if block.name == "classify_intent":
                    metadata = {
                        "intent_type": block.input.get("intent_type", "note"),
                        "should_store": block.input.get("should_store", False),
                        "due_hint": block.input.get("due_hint"),
                        "reminder_text": block.input.get("reminder_text"),
                    }
                else:
                    tool_use_blocks.append(block)

        if response.stop_reason == "end_turn":
            # Store simplified final turn in history
            history.append({"role": "assistant", "content": spoken or "(done)"})
            agentic_sessions[session_id] = history
            break

        if response.stop_reason == "tool_use" and tool_use_blocks:
            # Emit acknowledgment immediately so the user hears something while tools run.
            # Fall back to "On it." if the model didn't include a text block.
            yield {"type": "ack", "text": spoken or "On it."}
            spoken = ""  # don't re-use as final response

            # Append full assistant turn (preserves tool_use blocks for API)
            history.append({"role": "assistant", "content": response.content})

            # Execute each tool and collect results
            tool_results = []
            for block in tool_use_blocks:
                yield {"type": "tool_call", "name": block.name, "input": block.input}

                try:
                    tool_fn = TOOL_REGISTRY.get(block.name)
                    if tool_fn is None:
                        result = {"summary": f"Unknown tool: {block.name}", "error": True}
                    else:
                        result = await asyncio.wait_for(tool_fn(block.input), timeout=20.0)
                except asyncio.TimeoutError:
                    result = {"summary": f"{block.name} timed out after 20s", "error": True}
                except Exception as exc:
                    result = {"summary": f"{block.name} failed: {exc}", "error": True}

                yield {"type": "tool_result", "name": block.name, "summary": result.get("summary", ""), "data": result}

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

            # Also acknowledge classify_intent if it appeared in the same turn
            for block in response.content:
                if isinstance(block, ToolUseBlock) and block.name == "classify_intent":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({"status": "classified"}),
                    })

            history.append({"role": "user", "content": tool_results})

        else:
            # stop_reason is something unexpected — bail out
            history.append({"role": "assistant", "content": spoken or "(done)"})
            agentic_sessions[session_id] = history
            break

    else:
        spoken = "I hit my step limit. Try a simpler request."
        yield {"type": "spoken", "text": spoken}
        return

    yield {"type": "spoken", "text": spoken}
    yield {"type": "metadata", **metadata}

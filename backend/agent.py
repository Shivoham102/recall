import os
import json
import asyncio
import re
from datetime import datetime
from typing import AsyncGenerator
from anthropic import AsyncAnthropic
from anthropic.types import TextBlock, ToolUseBlock
from dotenv import load_dotenv
import context

load_dotenv()

async_client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Session store for the agentic /capture/stream endpoint
# Stores full content block lists so tool_use turns are preserved in history
agentic_sessions: dict[str, list[dict]] = {}

MAX_TURNS = 10
MAX_AGENT_ITERATIONS = 8

# ── Stable system prompt blocks (never inject datetime here — cache will miss) ──

SYSTEM_PROMPT_IDENTITY = """You are Recall, a voice assistant for working memory running on Windows 11.
You speak aloud — be extremely brief. Never explain, never repeat back what was said.
For simple captures (task/note/reminder): respond in 2-5 words. Examples: "Got it." "Sure." "Noted."
If the user asks to be reminded without specifying a clock time (hour/minute), ALWAYS ask: "What time?" — two words, nothing else. A date alone (e.g. "15th May", "tomorrow") is NOT sufficient — you must also have a clock time before storing.
For queries: one sentence maximum, specific facts only.
Never fabricate results — call the appropriate tool, then respond based on what it returns."""

SYSTEM_PROMPT_TOOL_RULES = """Tool usage rules:
- Call classify_intent on every turn where you give a spoken response without doing other agentic work.
- When asking a clarifying question (e.g. "When?"), call classify_intent with awaiting_clarification: true, should_store: false, and no due_hint. This is a hard storage gate — the backend will not store anything on that turn regardless of should_store.
- When completing a multi-turn sequence (all info now present after a follow-up), set awaiting_clarification: false, should_store: true. Include a "content" field with the full reconstructed intent (e.g. "do late code on 14th of May") — not just the follow-up detail ("10 a.m."). If the user corrected intent across turns, content reflects the final corrected version.
- For recall_update_item and calendar_create: speak the proposed action first without calling the tool. Wait for the user to confirm next turn, then execute.
- You cannot send emails — only save drafts. If the user asks to send, draft it and tell them it's saved as a draft.
- For email drafting, follow explicit user drafting preferences from the latest turn/session (short, concise, detailed, formal, casual) as highest-priority output constraints.
- For email drafting, never use em dashes. Prefer full stops, commas, or semicolons to break up sentences.
- For follow-up/reply requests: call gmail_find_followup_thread first (sent first, inbox fallback). If low confidence/no match, ask one clarifying question and do not draft yet.
- After a thread is selected, call gmail_get_thread_context before drafting and keep continuity with thread asks/commitments.
- For follow-up replies, prefer gmail_reply_draft over gmail_draft so the draft stays in the existing thread.
- IMPORTANT: Before the FIRST tool call in a user request, include a brief spoken acknowledgment (2-5 words) as a text block in the SAME response as the tool call. Examples: "Sure, on it." "Let me check." "On it." Do not repeat acknowledgments for subsequent tool calls in that same request."""

SYSTEM_PROMPT_BLOCKS = [
    {"type": "text", "text": SYSTEM_PROMPT_IDENTITY, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": SYSTEM_PROMPT_TOOL_RULES, "cache_control": {"type": "ephemeral"}},
]

_DRAFT_PREFS_BY_SESSION: dict[str, dict] = {}
_SHORT_RE = re.compile(r"\b(short|concise|brief|quick|one[- ]?liner)\b", re.IGNORECASE)
_DETAILED_RE = re.compile(r"\b(detailed|detail|thorough|comprehensive|longer)\b", re.IGNORECASE)
_FORMAL_RE = re.compile(r"\b(formal|professional|business[- ]?like)\b", re.IGNORECASE)
_CASUAL_RE = re.compile(r"\b(casual|friendly|relaxed|conversational)\b", re.IGNORECASE)


def _extract_draft_preferences(user_text: str) -> dict:
    text = user_text.strip()
    if not text:
        return {}

    preferences: dict[str, str] = {}
    if _SHORT_RE.search(text):
        preferences["length"] = "short"
    elif _DETAILED_RE.search(text):
        preferences["length"] = "detailed"

    if _FORMAL_RE.search(text):
        preferences["tone"] = "formal"
    elif _CASUAL_RE.search(text):
        preferences["tone"] = "casual"

    return preferences


def _augment_user_turn(user_text: str, rag_context: str, draft_preferences: dict | None = None) -> str:
    now = datetime.now().strftime("%A, %B %d %Y %H:%M")
    draft_pref_line = ""
    if draft_preferences:
        draft_pref_line = f"[Draft preferences: {json.dumps(draft_preferences)}]\n"
    return (
        f"[Date: {now}]\n"
        f"[Memory context:\n{rag_context}]\n\n"
        f"{draft_pref_line}"
        f"User: {user_text}"
    )


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
    from session_store import save_session, load_session

    if session_id not in agentic_sessions:
        agentic_sessions[session_id] = load_session(session_id)
    history = agentic_sessions[session_id]

    extracted_prefs = _extract_draft_preferences(user_text)
    session_prefs = dict(_DRAFT_PREFS_BY_SESSION.get(session_id, {}))
    if extracted_prefs:
        session_prefs.update(extracted_prefs)
        _DRAFT_PREFS_BY_SESSION[session_id] = session_prefs
    context.current_draft_preferences.set(session_prefs)
    context.current_style_ready.set(False)
    context.current_style_profile.set({})

    augmented_user = _augment_user_turn(user_text, rag_context, session_prefs)
    history.append({"role": "user", "content": augmented_user})

    if len(history) > MAX_TURNS * 2:
        history = history[-(MAX_TURNS * 2):]

    metadata = {"intent_type": "note", "should_store": False, "due_hint": None, "reminder_text": None, "content": None, "awaiting_clarification": False}
    spoken = ""
    ack_emitted = False
    ack_text = ""

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
                        "content": block.input.get("content"),
                        "awaiting_clarification": block.input.get("awaiting_clarification", False),
                    }
                else:
                    tool_use_blocks.append(block)

        if response.stop_reason == "end_turn":
            # Store simplified final turn in history
            history.append({"role": "assistant", "content": spoken or "(done)"})
            agentic_sessions[session_id] = history
            save_session(session_id, history)
            break

        if response.stop_reason == "tool_use" and tool_use_blocks:
            # Emit acknowledgment only once per user request (first tool-use turn).
            # Fall back to "On it." if the model didn't include a text block.
            if not ack_emitted:
                ack_text = spoken or "On it."
                yield {"type": "ack", "text": ack_text}
                ack_emitted = True
            spoken = ""  # don't re-use as final response; ack_text is fallback

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
            history_spoken = spoken or ack_text or ("Got it." if metadata.get("should_store") else "(done)")
            history.append({"role": "assistant", "content": history_spoken})
            agentic_sessions[session_id] = history
            save_session(session_id, history)
            break

    else:
        spoken = "I hit my step limit. Try a simpler request."
        yield {"type": "spoken", "text": spoken}
        return

    final_spoken = spoken or ack_text or ("Got it." if metadata.get("should_store") else "")
    yield {"type": "spoken", "text": final_spoken}
    yield {"type": "metadata", **metadata}

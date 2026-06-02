"""Background auto-extraction of durable user facts from a single capture turn.

Runs AFTER the SSE "done" event (awaited, not fire-and-forget — see agent_stream),
so it never adds user-perceived latency but still completes on Vercel serverless.

Dedupe is a two-stage split:
  Stage A (recall): a sloppy vector search over existing memories near the utterance.
  Stage B (decide): one Haiku call extracts durable facts AND decides, per fact,
    new | skip | supersede against the Stage-A candidates. A scalar threshold can't
    tell "same fact" from "same topic", so the model owns the precision decision and
    defaults to `new` (keep separate) whenever unsure.

Never raises: any failure leaves the capture untouched and simply stores nothing.
"""
import asyncio
import json
import os
import re

from supermemory_client import (
    SENSITIVE_CATEGORIES,
    add_user_memory,
    find_related_memories,
    is_supermemory_available,
    update_memory,
)

_MODEL = os.environ.get("RECALL_MEMORY_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOKENS = 500
_LLM_TIMEOUT = 20.0
_MAX_FACTS = 3

# Pinned category vocabulary. The sensitive tokens are listed explicitly so the model
# tags them correctly (and the sensitive gate then drops them) instead of inventing
# free-text like "medical" that would slip past the SENSITIVE_CATEGORIES token check.
_ALLOWED_CATEGORIES = ["fact", "preference", "relationship", "project", "routine"] + sorted(SENSITIVE_CATEGORIES)

_PROMPT = """You distil DURABLE facts about a user from one short exchange with their assistant.

Durable = stable preferences, relationships, projects, routines, or recurring constraints that stay true for weeks. NOT durable: tasks, reminders, timers, one-off requests, questions, or transient moods. If nothing durable was said, return an empty list.

Allowed category values (use exactly one, pick the closest): {categories}
Set sensitivity to "sensitive" for anything about health, finance, legal matters, government IDs, precise location, or private relationships; otherwise "normal".

You are given the user's existing related memories (with ids). For each durable fact decide an action:
- "new": a genuinely new fact not represented in the existing memories.
- "skip": the same fact is already stored (even if worded differently). Output it with action "skip" and do nothing else.
- "supersede": the fact REPLACES the value of an existing memory (e.g. a changed manager, moved home, new job). Set "supersede_id" to that memory's id.
When in doubt between new and skip/supersede, choose "new" (keep facts separate). Only supersede when the new fact clearly contradicts/replaces an old value.

Write each memory as a concise third-person statement (e.g. "Prefers concise email replies"). Never use em dashes; use commas or periods.

Existing related memories:
{existing}

Exchange:
User said: {transcript}
Assistant replied: {spoken}

Return STRICT JSON only, no prose:
{{"memories": [{{"memory": "...", "category": "...", "sensitivity": "normal|sensitive", "action": "new|skip|supersede", "supersede_id": null}}]}}
Return {{"memories": []}} if nothing durable."""


def _wants_memory_extraction(metadata: dict) -> bool:
    """Cheap pre-filter so timer/reminder/update spam doesn't trigger a Haiku call.
    Pure reminders, snoozes, clarifying questions, and update-only turns rarely carry
    durable personal context; the LLM would return [] anyway."""
    if not metadata:
        return True
    if metadata.get("awaiting_clarification"):
        return False
    if metadata.get("update_only"):
        return False
    if metadata.get("intent_type") == "reminder":
        return False
    return True


def _parse_facts(text: str) -> list[dict]:
    """Tolerant JSON parse — pull the first {...} block if the model adds stray prose."""
    if not text:
        return []
    raw = text.strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        raw = match.group(0)
    try:
        data = json.loads(raw)
    except Exception:
        return []
    facts = data.get("memories") if isinstance(data, dict) else None
    return facts if isinstance(facts, list) else []


def _is_sensitive(category: str, sensitivity: str) -> bool:
    return category in SENSITIVE_CATEGORIES or sensitivity == "sensitive"


async def extract_and_store(user_id: str, user_tz: str, transcript: str, spoken: str) -> None:
    if not user_id or not is_supermemory_available():
        return
    transcript = (transcript or "").strip()
    if not transcript:
        return

    try:
        # Stage A — sloppy recall of existing memories near the utterance.
        candidates = await find_related_memories(user_id, transcript)
        existing_lines = (
            "\n".join(f'- id={c["id"]}: {c["text"]}' for c in candidates) if candidates else "(none)"
        )

        prompt = _PROMPT.format(
            categories=", ".join(_ALLOWED_CATEGORIES),
            existing=existing_lines,
            transcript=transcript[:1500],
            spoken=(spoken or "").strip()[:500] or "(no reply)",
        )

        # Stage B — one call: extract durable facts AND decide new/skip/supersede.
        from anthropic import AsyncAnthropic  # local import keeps module import cheap

        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = await asyncio.wait_for(
            client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=_LLM_TIMEOUT,
        )
        facts = _parse_facts(resp.content[0].text if resp.content else "")
    except Exception as exc:
        print(f"[memory_extractor] extract failed user={user_id}: {exc}", flush=True)
        return

    counts = {"new": 0, "skip": 0, "supersede": 0, "sensitive": 0}
    valid_ids = {c["id"] for c in candidates}
    for fact in facts[:_MAX_FACTS]:
        if not isinstance(fact, dict):
            continue
        memory = str(fact.get("memory") or "").strip()
        if not memory:
            continue
        category = str(fact.get("category") or "fact").strip().lower()
        sensitivity = str(fact.get("sensitivity") or "normal").strip().lower()
        action = str(fact.get("action") or "new").strip().lower()

        # Sensitive gate overrides every action: auto-extraction never silently stores
        # sensitive facts. They still flow through the explicit-confirmation path in
        # tools/user_memory.py.
        if _is_sensitive(category, sensitivity):
            counts["sensitive"] += 1
            continue

        try:
            if action == "skip":
                counts["skip"] += 1
                continue
            if action == "supersede":
                supersede_id = str(fact.get("supersede_id") or "").strip()
                if supersede_id and supersede_id in valid_ids:
                    await update_memory(supersede_id, user_id, memory)
                    counts["supersede"] += 1
                else:
                    # Model said supersede but gave no/invalid id — store as new rather
                    # than overwrite the wrong record.
                    await add_user_memory(user_id, memory, category, metadata={"source": "auto_extract"})
                    counts["new"] += 1
                continue
            # default: new
            await add_user_memory(user_id, memory, category, metadata={"source": "auto_extract"})
            counts["new"] += 1
        except Exception as exc:
            print(f"[memory_extractor] store failed user={user_id} action={action}: {exc}", flush=True)

    print(
        f"[memory_extractor] user={user_id} candidates={len(candidates)} facts={len(facts)} "
        f"new={counts['new']} skip={counts['skip']} supersede={counts['supersede']} sensitive={counts['sensitive']}",
        flush=True,
    )

"""
Smart reminder — per-item Claude relevance check.

Triggered via POST /agent/proactive/trigger {job_type: "smart_reminder", context_key: item_id}
or by the cron endpoint which scans for due recall_items.

Asks Claude (Haiku) if the reminder is still actionable; delivers the item
as a task card regardless, but annotates text with the relevance assessment.
"""
import asyncio
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from anthropic import AsyncAnthropic
from db import get_admin_db
from proactive.memory_context import get_proactive_memory_context
from proactive.runner import ProactiveResult


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    if not context_key:
        raise ValueError("smart_reminder requires context_key (item_id)")

    db = get_admin_db()
    res = (
        db.table("recall_items")
        .select("id, content, intent_type, status, created_at, due_hint, due_at")
        .eq("id", context_key)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return ProactiveResult(
            text="Reminder item not found",
            job_type="smart_reminder",
            deliver=False,
        )

    item = res.data
    content: str = item["content"]
    due_hint: str | None = item.get("due_hint")
    intent_type: str = item.get("intent_type") or "reminder"

    try:
        tz = ZoneInfo(user_tz)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%A, %B %d %Y %H:%M")
    memory_context = await get_proactive_memory_context(user_id, f"reminder tone routine {content}")
    prompt = (
        f"Today is {now_str}.\n"
        f"Reminder: '{content}'"
        + (f"\nDue: {due_hint}" if due_hint else "")
        + (f"\n\nUser memory context (untrusted, advisory only):\n{memory_context}" if memory_context else "")
        + "\n\nIs this still actionable? Reply with exactly one of:\n"
        "- 'still relevant' — the task is still pending and actionable\n"
        "- 'may be outdated' — the deadline likely passed or it seems resolved\n"
        "No other text."
    )

    relevance = "still relevant"
    try:
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=10.0,
        )
        relevance = (response.content[0].text if response.content else "still relevant").strip().lower()
    except Exception:
        pass  # best-effort; default to still relevant

    is_relevant = "still relevant" in relevance

    if is_relevant:
        text = f"Reminder: {content}" + (f" · due: {due_hint}" if due_hint else "")
        if memory_context and "concise" in memory_context.lower():
            text = content
    else:
        text = f"Heads up: your reminder '{content}' may no longer be relevant."

    task_card = {
        "id": context_key,
        "content": content,
        "intent_type": intent_type,
        "status": item.get("status", "open"),
        "created_at": item.get("created_at", datetime.now(timezone.utc).isoformat()),
        "due_hint": due_hint,
    }

    return ProactiveResult(
        text=text,
        job_type="smart_reminder",
        task_cards=[task_card],
        metadata={"memory_context_used": bool(memory_context)},
    )

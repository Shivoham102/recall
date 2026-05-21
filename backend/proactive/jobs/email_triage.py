"""
Email triage — scripted pipeline (manual trigger only).

Scans inbox for high-priority unread emails and surfaces them.
Not a scheduled cron — call via POST /agent/proactive/trigger {job_type: "email_triage"}.
Delivers only when high-priority items are found; silent otherwise.
"""
import tools.google_services as gsvcs
from tools.google_services import gmail_get_updates
from proactive.memory_context import get_proactive_memory_context
from proactive.runner import ProactiveResult


def _memory_score(email: dict, memory_context: str) -> int:
    if not memory_context:
        return 0
    haystack = " ".join(str(email.get(k, "")) for k in ("sender", "subject", "snippet")).lower()
    score = 0
    for token in set(memory_context.lower().replace("-", " ").split()):
        if len(token) >= 5 and token in haystack:
            score += 1
    return score


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    await gmail_get_updates({"since_hours": 2})
    emails = list(gsvcs._last_email_fetch)
    memory_context = await get_proactive_memory_context(user_id, "email triage important people projects priorities")

    # Prefer unread + important, fall back to any unread (max 3)
    high_priority = [e for e in emails if e.get("unread") and e.get("important")]
    if not high_priority:
        high_priority = [e for e in emails if e.get("unread")][:3]
    if memory_context and high_priority:
        high_priority = sorted(high_priority, key=lambda e: _memory_score(e, memory_context), reverse=True)[:3]

    if not high_priority:
        return ProactiveResult(
            text="Email triage — no high-priority items",
            job_type="email_triage",
            deliver=False,
        )

    count = len(high_priority)
    text = f"Email triage — {count} item{'s' if count != 1 else ''} need{'s' if count == 1 else ''} attention"
    return ProactiveResult(
        text=text,
        job_type="email_triage",
        email_cards=high_priority,
        metadata={"memory_context_used": bool(memory_context)},
    )

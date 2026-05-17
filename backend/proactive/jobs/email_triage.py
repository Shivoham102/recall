"""
Email triage — scripted pipeline (manual trigger only).

Scans inbox for high-priority unread emails and surfaces them.
Not a scheduled cron — call via POST /agent/proactive/trigger {job_type: "email_triage"}.
Delivers only when high-priority items are found; silent otherwise.
"""
import tools.google_services as gsvcs
from tools.google_services import gmail_get_updates
from proactive.runner import ProactiveResult


async def run(user_id: str, context_key: str | None = None) -> ProactiveResult:
    await gmail_get_updates({"since_hours": 2})
    emails = list(gsvcs._last_email_fetch)

    # Prefer unread + important, fall back to any unread (max 3)
    high_priority = [e for e in emails if e.get("unread") and e.get("important")]
    if not high_priority:
        high_priority = [e for e in emails if e.get("unread")][:3]

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
    )

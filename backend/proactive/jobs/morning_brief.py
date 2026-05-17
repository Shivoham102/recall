"""
Morning brief — scripted pipeline (no full agentic loop).

Steps:
  1. calendar_list (today's events)
  2. gmail_get_updates (last 8h)
  3. recall_search (open tasks/reminders)
  4. Format a brief header text + return cards for UI rendering.
"""
from datetime import datetime, timezone

from proactive.runner import ProactiveResult
from tools.memory import recall_search
import tools.memory as memory_module

_google_available = False
try:
    import tools.google_services as gsvcs
    from tools.google_services import calendar_list, gmail_get_updates
    _google_available = True
except ImportError:
    pass


async def run(user_id: str, context_key: str | None = None) -> ProactiveResult:
    today = datetime.now(timezone.utc).strftime("%A, %B %d")

    calendar_cards: list[dict] = []
    email_cards: list[dict] = []

    if _google_available:
        # Calendar — today only
        await calendar_list({"days_ahead": 1})
        calendar_cards = list(gsvcs._last_calendar_fetch)

        # Email — last 8h
        await gmail_get_updates({"since_hours": 8})
        email_cards = list(gsvcs._last_email_fetch)

    # Open tasks/reminders
    await recall_search({"query": "due task reminder deadline urgent", "limit": 5, "status": "open"})
    task_cards = list(memory_module._last_task_fetch)

    # Build summary text (header only — cards carry the content)
    parts: list[str] = [f"Morning brief — {today}"]
    if calendar_cards:
        parts.append(f"{len(calendar_cards)} event{'s' if len(calendar_cards) != 1 else ''} today")
    if email_cards:
        parts.append(f"{len(email_cards)} new email{'s' if len(email_cards) != 1 else ''}")
    if task_cards:
        parts.append(f"{len(task_cards)} open task{'s' if len(task_cards) != 1 else ''}")
    if len(parts) == 1:
        parts.append("Nothing urgent on the radar.")

    return ProactiveResult(
        text=" · ".join(parts),
        job_type="morning_brief",
        email_cards=email_cards,
        calendar_cards=calendar_cards,
        task_cards=task_cards,
    )

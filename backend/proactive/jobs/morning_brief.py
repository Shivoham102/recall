"""
Morning brief — LLM-judged pipeline (runs daily at 7am).

Fetches calendar, email, and open tasks then runs a headless Claude loop
that curates and surfaces only genuinely relevant items as cards.
Promotional emails, newsletters, and automated alerts are filtered out.
Email lookback window grows if user was away multiple days (capped at 72h).
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import get_admin_db
from proactive.memory_context import get_proactive_memory_context
from proactive.loop import run_headless_loop
from proactive.runner import ProactiveResult

_SYSTEM_PROMPT_TEMPLATE = """\
You are generating a morning brief. Curate ruthlessly — show only what genuinely matters.

Run these three tool calls in order:
1. calendar_list with days_ahead=1
2. gmail_get_updates with since_hours={since_hours}
3. recall_search with query="due task urgent deadline reminder" and limit=5 and status="open"

Then surface selectively:
- surface_calendar: all events today (every one you find)
- surface_cards: ONLY real person-to-person emails. Skip ALL of these: newsletters, \
promotions, marketing, job boards, automated alerts, shopping, subscriptions, \
"no-reply", discount offers, product updates. If every email is promotional, \
call surface_cards with indices=[] (empty — do not surface junk).
- surface_tasks: only tasks that are overdue or due today

Finally write ONE summary line. Format exactly:
  Morning brief — {{weekday}}, {{month}} {{day}} · {{N}} event(s) · {{N}} email(s) · {{N}} task(s)
Omit a section if count is zero (e.g. no tasks → omit "· 0 tasks").
Write nothing else — no greetings, no explanations.\
"""

_MEMORY_INSTRUCTIONS = """

Use the user profile context below only to prioritize and phrase the brief.
Do not invent facts from memory; all surfaced items must come from calendar, email, or Recall tasks.

{memory_context}
"""


def _last_delivery_hours_ago(user_id: str) -> int:
    """Hours since last delivered morning brief for this user, capped at 72. Default 24 on first run."""
    db = get_admin_db()
    res = (
        db.table("proactive_jobs")
        .select("finished_at")
        .eq("user_id", user_id)
        .eq("job_type", "morning_brief")
        .eq("status", "done")
        .eq("delivered", True)
        .order("finished_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return 24
    raw = res.data[0]["finished_at"]
    last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return min(72, max(8, int((datetime.now(timezone.utc) - last).total_seconds() / 3600)))


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    from tools import PROACTIVE_TOOL_DEFINITIONS, PROACTIVE_TOOL_REGISTRY  # noqa: PLC0415

    since_hours = _last_delivery_hours_ago(user_id)
    print(f"[morning_brief] start user={user_id} since_hours={since_hours}")
    memory_context = await get_proactive_memory_context(user_id, "morning brief email calendar tasks priorities")
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(since_hours=since_hours)
    if memory_context:
        system_prompt += _MEMORY_INSTRUCTIONS.format(memory_context=memory_context)

    try:
        tz = ZoneInfo(user_tz)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).strftime("%A, %B %d")
    user_message = f"Today is {today}. Generate the morning brief now."

    loop_result = await run_headless_loop(
        system_prompt=system_prompt,
        user_message=user_message,
        tool_definitions=PROACTIVE_TOOL_DEFINITIONS,
        tool_registry=PROACTIVE_TOOL_REGISTRY,
    )

    text = loop_result.text or f"Morning brief — {today}"
    print(f"[morning_brief] loop done text_len={len(text)}")

    return ProactiveResult(
        text=text,
        job_type="morning_brief",
        email_cards=loop_result.email_cards,
        calendar_cards=loop_result.calendar_cards,
        task_cards=loop_result.task_cards,
    )

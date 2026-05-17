"""
Morning brief — LLM-judged pipeline (runs daily at 7am).

Fetches calendar, email, and open tasks then runs a headless Claude loop
that curates and surfaces only genuinely relevant items as cards.
Promotional emails, newsletters, and automated alerts are filtered out.
"""
from datetime import datetime, timezone

from proactive.loop import run_headless_loop
from proactive.runner import ProactiveResult

_SYSTEM_PROMPT = """\
You are generating a morning brief. Curate ruthlessly — show only what genuinely matters.

Run these three tool calls in order:
1. calendar_list with days_ahead=1
2. gmail_get_updates with since_hours=8
3. recall_search with query="due task urgent deadline reminder" and limit=5 and status="open"

Then surface selectively:
- surface_calendar: all events today (every one you find)
- surface_cards: ONLY real person-to-person emails. Skip ALL of these: newsletters, \
promotions, marketing, job boards, automated alerts, shopping, subscriptions, \
"no-reply", discount offers, product updates. If every email is promotional, \
call surface_cards with indices=[] (empty — do not surface junk).
- surface_tasks: only tasks that are overdue or due today

Finally write ONE summary line. Format exactly:
  Morning brief — {weekday}, {month} {day} · {N} event(s) · {N} email(s) · {N} task(s)
Omit a section if count is zero (e.g. no tasks → omit "· 0 tasks").
Write nothing else — no greetings, no explanations.\
"""


async def run(user_id: str, context_key: str | None = None) -> ProactiveResult:
    from tools import PROACTIVE_TOOL_DEFINITIONS, PROACTIVE_TOOL_REGISTRY  # noqa: PLC0415

    today = datetime.now(timezone.utc).strftime("%A, %B %d")
    user_message = f"Today is {today}. Generate the morning brief now."

    loop_result = await run_headless_loop(
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
        tool_definitions=PROACTIVE_TOOL_DEFINITIONS,
        tool_registry=PROACTIVE_TOOL_REGISTRY,
    )

    text = loop_result.text or f"Morning brief — {today}"

    return ProactiveResult(
        text=text,
        job_type="morning_brief",
        email_cards=loop_result.email_cards,
        calendar_cards=loop_result.calendar_cards,
        task_cards=loop_result.task_cards,
    )

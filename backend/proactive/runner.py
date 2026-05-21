import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import context
from db import get_admin_db


@dataclass
class ProactiveResult:
    text: str
    job_type: str
    email_cards: list[dict] = field(default_factory=list)
    calendar_cards: list[dict] = field(default_factory=list)
    task_cards: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    deliver: bool = True  # False = mark done+delivered immediately, skip SSE


# Jobs that dedupe by calendar day (UTC) rather than a rolling window.
# Ensures the 7am cron always runs even if user manually triggered earlier that day.
_CALENDAR_DAY_JOBS = {"morning_brief", "pattern_learn"}

# How far back to look for an existing successful run before allowing a new one.
_DEDUPE_WINDOWS: dict[str, timedelta] = {
    "morning_brief": timedelta(days=1),   # overridden by _CALENDAR_DAY_JOBS
    "pattern_learn": timedelta(days=1),   # overridden by _CALENDAR_DAY_JOBS
    "email_triage": timedelta(minutes=90),
    "follow_up_scan": timedelta(minutes=50),
}

MAX_RETRIES = 3

# In-process locks prevent two concurrent invocations of the same (user, job) pair.
_locks: dict[str, asyncio.Lock] = {}
_locks_mu = asyncio.Lock()


async def _get_lock(key: str) -> asyncio.Lock:
    async with _locks_mu:
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        return _locks[key]


async def run_job(
    user_id: str,
    job_type: str,
    context_key: str | None = None,
    user_tz: str = "UTC",
) -> ProactiveResult | None:
    """
    Run a proactive job for the given user. Returns ProactiveResult on success, None if skipped.

    DB lifecycle:
      - done row in window        → skip (already succeeded)
      - failed row                → skip (terminal failure after MAX_RETRIES)
      - running row, retries < 3  → retry execution
      - no row                    → insert, then execute
    retry_count is incremented only on failure, not on each attempt.
    """
    lock_key = f"{user_id}:{job_type}:{context_key or ''}"
    lock = await _get_lock(lock_key)
    if lock.locked():
        return None

    async with lock:
        db = get_admin_db()
        now = datetime.now(timezone.utc)

        # ── 1. Check for existing row ─────────────────────────────────────────
        existing: dict | None = None

        window = _DEDUPE_WINDOWS.get(job_type)
        if window is not None:
            if job_type in _CALENDAR_DAY_JOBS:
                try:
                    _tz_obj = ZoneInfo(user_tz)
                except ZoneInfoNotFoundError:
                    _tz_obj = ZoneInfo("UTC")
                _local_now = now.astimezone(_tz_obj)
                _local_midnight = _local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                window_start = _local_midnight.astimezone(timezone.utc).isoformat()
            else:
                window_start = (now - window).isoformat()
            res = (
                db.table("proactive_jobs")
                .select("*")
                .eq("user_id", user_id)
                .eq("job_type", job_type)
                .gte("started_at", window_start)
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
        else:
            res = None

        if res and res.data:
            existing = res.data[0]

        # ── 2. Decide: skip, retry, or insert ────────────────────────────────
        job_id: str
        if existing:
            if existing["status"] == "done":
                return None  # already succeeded in window
            if existing["status"] == "failed":
                return None  # terminal failure, no more retries
            # status == "running": retry if retries remain
            if existing["retry_count"] >= MAX_RETRIES:
                return None
            job_id = existing["id"]
        else:
            insert_res = (
                db.table("proactive_jobs")
                .insert({
                    "user_id": user_id,
                    "job_type": job_type,
                    "context_key": context_key,
                    "status": "running",
                    "retry_count": 0,
                })
                .execute()
            )
            job_id = insert_res.data[0]["id"]

        # ── 3. Set ContextVars so tools can find the user ─────────────────────
        ctx_tok = context.current_user_id.set(user_id)
        tz_tok = context.current_user_tz.set(user_tz)
        context.current_style_ready.set(False)
        context.current_style_profile.set({})
        context.current_draft_preferences.set({})

        try:
            # ── 4. Dispatch to the job implementation ─────────────────────────
            from proactive.jobs import get_job_fn  # lazy to avoid circular import
            job_fn = get_job_fn(job_type)
            result: ProactiveResult = await job_fn(user_id, context_key=context_key, user_tz=user_tz)

            db.table("proactive_jobs").update({
                "status": "done",
                "delivered": not result.deliver,
                "result": {
                    "text": result.text,
                    "email_cards": result.email_cards,
                    "calendar_cards": result.calendar_cards,
                    "task_cards": result.task_cards,
                    "metadata": result.metadata,
                },
                "finished_at": now.isoformat(),
            }).eq("id", job_id).execute()

            return result

        except Exception as exc:
            current_retries = (existing["retry_count"] if existing else 0)
            new_retries = current_retries + 1
            update: dict = {
                "retry_count": new_retries,
                "error": str(exc),
            }
            if new_retries >= MAX_RETRIES:
                update["status"] = "failed"
                update["finished_at"] = now.isoformat()
            db.table("proactive_jobs").update(update).eq("id", job_id).execute()
            return None

        finally:
            context.current_user_id.reset(ctx_tok)
            context.current_user_tz.reset(tz_tok)

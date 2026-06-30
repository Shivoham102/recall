import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import context
from db import get_admin_db

logger = logging.getLogger(__name__)


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
    "follow_up_draft": timedelta(hours=20),
}

MAX_RETRIES = 3

# Reclaim lease for a stuck 'running' row (passed to claim_proactive_job). MUST stay strictly
# greater than the caller's per-job timeout (cron.py JOB_TIMEOUT_SECONDS, ~270s) so a job that is
# legitimately in flight is never reclaimed mid-run.
JOB_LEASE_SECONDS = 300

# follow_up_scan and follow_up_draft both mutate the same follow_up_threads rows / Gmail
# drafts for a user. scan keys its lock by nothing (no single thread), draft keys by thread id,
# so a per-(job_type, context_key) lock never serializes them and they can race on draft_gmail_id.
# Collapse the whole family onto one per-user lock key so scan and draft never overlap.
_LOCK_FAMILY = {"follow_up_scan": "follow_up", "follow_up_draft": "follow_up"}

# In-process locks prevent two concurrent invocations of the same (user, job) pair.
# NOTE: in-process only — serializes within one warm serverless instance, not across instances.
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
    # Follow-up family shares one per-user lock (drop context_key) so scan and draft serialize;
    # all other jobs keep the per-(job, context) key.
    family = _LOCK_FAMILY.get(job_type)
    lock_key = f"{user_id}:{family}" if family else f"{user_id}:{job_type}:{context_key or ''}"
    lock = await _get_lock(lock_key)
    if lock.locked():
        return None

    async with lock:
        db = get_admin_db()
        now = datetime.now(timezone.utc)

        # ── 1. Compute the dedupe window start (None = windowless job → no time-dedup) ──
        window = _DEDUPE_WINDOWS.get(job_type)
        window_start: str | None = None
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

        # ── 2. Atomic claim (cross-instance) ──────────────────────────────────
        # One transaction decides skip / retry / claim, so two racing invocations (cron retry,
        # overlapping /trigger, fan-out re-fire) can never both run the same (user, job). The
        # in-process lock above is only a same-instance fast-path; this is the real guarantee.
        claim = db.rpc("claim_proactive_job", {
            "p_user_id": user_id,
            "p_job_type": job_type,
            "p_context_key": context_key,
            "p_window_start": window_start,
            "p_lease_seconds": JOB_LEASE_SECONDS,
            "p_max_retries": MAX_RETRIES,
        }).execute()
        claimed = (claim.data or [None])[0]
        action = claimed.get("action") if claimed else None
        if action not in ("claimed", "retry"):
            return None  # skip_done / skip_running / skip_terminal
        job_id = claimed["job_id"]
        claimed_retries = claimed.get("retry_count") or 0

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
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job_id).execute()

            return result

        except asyncio.CancelledError:
            # Hard timeout (the caller's asyncio.wait_for) cancels via CancelledError, a
            # BaseException that `except Exception` below does NOT catch. Mirror the failure
            # handler: advance the retry budget (so repeated timeouts converge to terminal) and,
            # if exhausted, mark failed to FREE the unique running-slot. Non-terminal timeouts
            # leave the row 'running' for the lease-based reclaim to pick up next dispatch.
            # Sync db…execute() writes only — no await that could re-trigger cancellation.
            new_retries = claimed_retries + 1
            update: dict = {"retry_count": new_retries, "error": "timeout"}
            if new_retries >= MAX_RETRIES:
                update["status"] = "failed"
                update["finished_at"] = datetime.now(timezone.utc).isoformat()
            db.table("proactive_jobs").update(update).eq("id", job_id).execute()
            logger.warning(
                "proactive job %s timed out for user %s (attempt %d/%d)",
                job_type, user_id, new_retries, MAX_RETRIES,
            )
            raise

        except Exception as exc:
            current_retries = claimed_retries
            new_retries = current_retries + 1
            update: dict = {
                "retry_count": new_retries,
                "error": str(exc),
            }
            if new_retries >= MAX_RETRIES:
                update["status"] = "failed"
                update["finished_at"] = datetime.now(timezone.utc).isoformat()
            terminal = new_retries >= MAX_RETRIES
            db.table("proactive_jobs").update(update).eq("id", job_id).execute()
            if terminal:
                # Terminal failure was previously only visible as a bare str(exc) printed by
                # the dispatch loop (and not at all for direct /trigger calls). Log with the
                # traceback so it is diagnosable in Vercel logs instead of vanishing.
                logger.error(
                    "proactive job %s failed terminally for user %s after %d attempts",
                    job_type, user_id, new_retries, exc_info=True,
                )
                raise  # only surface terminal failures; retryable ones return None
            logger.warning(
                "proactive job %s failed for user %s (attempt %d/%d), will retry: %s",
                job_type, user_id, new_retries, MAX_RETRIES, exc,
            )
            return None

        finally:
            context.current_user_id.reset(ctx_tok)
            context.current_user_tz.reset(tz_tok)

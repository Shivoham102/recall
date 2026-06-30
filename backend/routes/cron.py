import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from tools.google_services import refresh_style_profiles_weekly
from db import get_admin_db
from proactive.runner import run_job

router = APIRouter()

# Per-job ceiling inside a single /jobs/run-one invocation (the job now owns its own function,
# so this is well under the 300s function cap). MUST stay below the runner's reclaim lease (300s)
# so a job killed at this timeout is not also reclaimed as "stale" mid-run.
JOB_TIMEOUT_SECONDS = 270

# How long the dispatcher waits on each fanned-out child before giving up on its HTTP call. The
# child is a separate invocation with its own JOB_TIMEOUT_SECONDS, so this is just a backstop;
# a child that exceeds it may still finish on its own (dispatch counts are advisory).
CHILD_TIMEOUT_SECONDS = 285


def _self_base_url() -> str:
    """Base URL to call our own deployment for fan-out. Vercel sets VERCEL_URL (host only)."""
    vercel_url = os.environ.get("VERCEL_URL", "").strip()
    if vercel_url:
        return f"https://{vercel_url}"
    port = os.environ.get("BACKEND_PORT", "").strip() or "8000"
    return f"http://127.0.0.1:{port}"


async def _fan_out(jobs: list[tuple[str, str, str]]) -> list[str]:
    """POST one /jobs/run-one per (user_id, job_type, tz) so each runs in its own invocation,
    concurrently. Returns one outcome string per job (ran | skipped | failed)."""
    if not jobs:
        return []
    secret = os.environ.get("CRON_SECRET", "").strip()
    base = _self_base_url()
    headers = {"x-cron-secret": secret}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
    if bypass:  # only needed if Vercel Deployment Protection is enabled
        headers["x-vercel-protection-bypass"] = bypass

    async def _one(client: httpx.AsyncClient, user_id: str, job_type: str, tz: str) -> str:
        try:
            r = await asyncio.wait_for(
                client.post(
                    f"{base}/jobs/run-one",
                    json={"user_id": user_id, "job_type": job_type, "tz": tz},
                    headers=headers,
                ),
                timeout=CHILD_TIMEOUT_SECONDS,
            )
            if r.status_code == 200:
                return r.json().get("outcome", "ran")
            print(f"[dispatch] run-one {job_type} for {user_id}: HTTP {r.status_code}")
            return "failed"
        except Exception as exc:
            print(f"[dispatch] run-one {job_type} for {user_id} errored: {exc}")
            return "failed"

    async with httpx.AsyncClient(timeout=CHILD_TIMEOUT_SECONDS + 10) as client:
        return list(await asyncio.gather(*[_one(client, u, j, t) for (u, j, t) in jobs]))


def _is_authorized(request: Request, authorization: str | None, x_cron_secret: str | None) -> bool:
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        return False

    bearer_ok = authorization == f"Bearer {secret}"
    header_ok = x_cron_secret == secret
    vercel_signature_ok = request.headers.get("x-vercel-cron-secret") == secret
    return bearer_ok or header_ok or vercel_signature_ok


@router.get("/jobs/refresh-email-style-profiles")
async def refresh_email_style_profiles(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")

    result = await refresh_style_profiles_weekly({"max_users": 500})
    return {
        "ok": True,
        **result,
    }


INACTIVITY_DAYS = 14


def _is_active(row: dict) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=INACTIVITY_DAYS)
    last = row.get("last_checkin_at")
    if last:
        return datetime.fromisoformat(last.replace("Z", "+00:00")) >= cutoff
    # Never checked in — treat as active only if account is new
    created = row.get("created_at", "")
    return bool(created and datetime.fromisoformat(created.replace("Z", "+00:00")) >= cutoff)


async def _run_job_for_all_users(job_type: str, context_key: str | None = None) -> dict:
    """Run a proactive job for every active user that has Google credentials configured."""
    db = get_admin_db()
    res = (
        db.table("users")
        .select("id, last_checkin_at, created_at, timezone")
        .not_.is_("google_access_token", "null")
        .execute()
    )
    all_users = res.data or []
    users = [r for r in all_users if _is_active(r)]
    ran, skipped, failed = 0, 0, 0
    for row in users:
        try:
            result = await run_job(row["id"], job_type, context_key, user_tz=row.get("timezone") or "UTC")
            if result is None:
                skipped += 1
            else:
                ran += 1
        except Exception as exc:
            failed += 1
            print(f"[cron] {job_type} failed for user {row['id']}: {exc}")
    return {"ran": ran, "skipped": skipped, "failed": failed, "total_users": len(all_users), "inactive_skipped": len(all_users) - len(users)}


@router.get("/jobs/morning-brief")
async def morning_brief_job(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    result = await _run_job_for_all_users("morning_brief")
    return {"ok": True, **result}


@router.get("/jobs/email-triage")
async def email_triage_job(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    result = await _run_job_for_all_users("email_triage")
    return {"ok": True, **result}


@router.get("/jobs/follow-up-draft")
async def follow_up_draft_job(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    result = await _run_job_for_all_users("follow_up_draft")
    return {"ok": True, **result}


@router.get("/jobs/follow-up-scan")
async def follow_up_scan_job(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    result = await _run_job_for_all_users("follow_up_scan")
    return {"ok": True, **result}


@router.get("/jobs/pattern-learn")
async def pattern_learn_job(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    result = await _run_job_for_all_users("pattern_learn")
    return {"ok": True, **result}


# Local-time offsets (hours before morning_brief_hour) for the morning pipeline.
# Mirrors the old fixed UTC schedule (02/05/06/07) but anchored to each user's
# local brief hour, so scan/draft always precede the brief in every timezone.
_PIPELINE_OFFSETS = {
    "pattern_learn": 5,
    "follow_up_scan": 2,
    "follow_up_draft": 1,
    "morning_brief": 0,
}


def _due_jobs(local_hour: int, brief_hour: int, brief_enabled: bool) -> list[str]:
    """Job types whose local target hour matches local_hour (mod 24, DST-safe)."""
    due = []
    for job, offset in _PIPELINE_OFFSETS.items():
        if (brief_hour - offset) % 24 != local_hour:
            continue
        if job == "morning_brief" and not brief_enabled:
            continue
        due.append(job)
    return due


class RunOneRequest(BaseModel):
    user_id: str
    job_type: str
    tz: str = "UTC"


@router.post("/jobs/run-one")
async def run_one(
    body: RunOneRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    """Run exactly ONE proactive job for ONE user. Each call is its own function invocation, so
    the job owns a full time budget. Fanned out by /jobs/dispatch; secret-gated like every job."""
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    try:
        result = await asyncio.wait_for(
            run_job(body.user_id, body.job_type, user_tz=body.tz),
            timeout=JOB_TIMEOUT_SECONDS,
        )
        return {"ok": True, "outcome": "ran" if result is not None else "skipped"}
    except asyncio.TimeoutError:
        print(f"[run-one] {body.job_type} timed out for user {body.user_id}")
        return {"ok": False, "outcome": "timeout"}
    except Exception as exc:
        print(f"[run-one] {body.job_type} failed for user {body.user_id}: {exc}")
        return {"ok": False, "outcome": "error"}


@router.get("/jobs/dispatch")
async def dispatch_jobs(
    request: Request,
    h: int | None = None,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    """Hourly dispatcher (one cron per UTC hour, `?h=N`). Computes which pipeline jobs are due at
    each active user's local hour, then FANS OUT one /jobs/run-one invocation per (user, job) so
    they run in parallel, each with its own time budget. The dispatcher itself does no job work."""
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")

    # Pin to the intended UTC hour so within-the-hour jitter doesn't shift the
    # computed local hour (matters for half-hour-offset timezones).
    now = datetime.now(timezone.utc)
    hour = h if h is not None else now.hour
    base = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    db = get_admin_db()
    res = (
        db.table("users")
        .select("id, last_checkin_at, created_at, timezone, morning_brief_hour, proactive_morning_brief, google_reauth_required")
        .not_.is_("google_access_token", "null")
        .execute()
    )
    all_users = res.data or []
    users = [r for r in all_users if _is_active(r)]

    reauth_count = sum(1 for r in all_users if r.get("google_reauth_required"))
    if reauth_count:
        print(f"[dispatch] {reauth_count} users need Google reconnect")

    # Compute the due (user, job, tz) tuples — same local-hour routing as before.
    jobs: list[tuple[str, str, str]] = []
    for row in users:
        tz_name = row.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        local_hour = base.astimezone(tz).hour
        brief_hour = row.get("morning_brief_hour")
        brief_hour = 7 if brief_hour is None else brief_hour
        brief_enabled = bool(row.get("proactive_morning_brief", True))
        for job_type in _due_jobs(local_hour, brief_hour, brief_enabled):
            jobs.append((row["id"], job_type, tz_name))

    outcomes = await _fan_out(jobs)
    ran = outcomes.count("ran")
    skipped = outcomes.count("skipped")
    failed = len(outcomes) - ran - skipped  # timeout/error/HTTP failures — advisory only

    return {
        "ok": True,
        "hour": hour,
        "dispatched": len(jobs),
        "ran": ran,
        "skipped": skipped,
        "failed": failed,
        "total_users": len(all_users),
        "inactive_skipped": len(all_users) - len(users),
    }


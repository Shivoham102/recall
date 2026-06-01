import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Header, HTTPException, Request
from tools.google_services import refresh_style_profiles_weekly
from db import get_admin_db
from proactive.runner import run_job

router = APIRouter()


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


@router.get("/jobs/dispatch")
async def dispatch_jobs(
    request: Request,
    h: int | None = None,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    """Hourly dispatcher (one cron per UTC hour, `?h=N`). For each active user,
    run whichever pipeline jobs are due at the user's current local hour."""
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
        .select("id, last_checkin_at, created_at, timezone, morning_brief_hour, proactive_morning_brief")
        .not_.is_("google_access_token", "null")
        .execute()
    )
    all_users = res.data or []
    users = [r for r in all_users if _is_active(r)]

    ran, skipped, failed, dispatched = 0, 0, 0, 0
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
            dispatched += 1
            try:
                result = await run_job(row["id"], job_type, user_tz=tz_name)
                if result is None:
                    skipped += 1
                else:
                    ran += 1
            except Exception as exc:
                failed += 1
                print(f"[dispatch] {job_type} failed for user {row['id']}: {exc}")

    return {
        "ok": True,
        "hour": hour,
        "dispatched": dispatched,
        "ran": ran,
        "skipped": skipped,
        "failed": failed,
        "total_users": len(all_users),
        "inactive_skipped": len(all_users) - len(users),
    }


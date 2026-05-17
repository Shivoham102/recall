import os
from datetime import datetime, timedelta, timezone
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
        .select("id, last_checkin_at, created_at")
        .not_.is_("google_access_token", "null")
        .execute()
    )
    all_users = res.data or []
    users = [r for r in all_users if _is_active(r)]
    ran, skipped, failed = 0, 0, 0
    for row in users:
        result = await run_job(row["id"], job_type, context_key)
        if result is None:
            skipped += 1
        else:
            ran += 1
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


@router.get("/jobs/smart-reminder")
async def smart_reminder_job(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    """Scan for recall_items with due_at in the next 30 min and trigger smart_reminder per item."""
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")

    db = get_admin_db()
    now = datetime.now(timezone.utc)
    window_end = (now + timedelta(minutes=30)).isoformat()

    res = (
        db.table("recall_items")
        .select("id, user_id")
        .eq("status", "open")
        .lte("due_at", window_end)
        .gte("due_at", now.isoformat())
        .execute()
    )
    items = res.data or []
    ran, skipped = 0, 0
    for item in items:
        result = await run_job(item["user_id"], "smart_reminder", context_key=item["id"])
        if result is None:
            skipped += 1
        else:
            ran += 1

    return {"ok": True, "ran": ran, "skipped": skipped, "total_items": len(items)}

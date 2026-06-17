"""
Proactive agent delivery routes.

Delivery is via Supabase Realtime: clients subscribe to their own proactive_jobs
rows directly. These endpoints support that flow (init/backlog drain, announce
audio, ack) rather than holding a long-lived connection.

GET  /agent/proactive/init           — proactive_chat_id + undelivered backlog; bumps last_checkin_at
GET  /agent/proactive/announce-audio — TTS for the "morning brief ready" announcement
POST /agent/proactive/ack            — Mark a job as delivered (body: {id: str})
POST /agent/proactive/trigger        — Manually trigger a job (body: {job_type: str, context_key?: str})
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from db import get_admin_db
from proactive.runner import run_job
from tts import synthesize

router = APIRouter()

_PROACTIVE_INBOX_TITLE = "Recall"


def _get_or_create_proactive_inbox(user_id: str) -> str:
    """Return the agent_chats.id for this user's proactive inbox, creating it if absent."""
    db = get_admin_db()
    res = (
        db.table("agent_chats")
        .select("id")
        .eq("user_id", user_id)
        .eq("is_proactive_inbox", True)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["id"]

    ts = datetime.now(timezone.utc).isoformat()
    insert_res = (
        db.table("agent_chats")
        .insert({
            "user_id": user_id,
            "agent_session_id": f"proactive:inbox:{user_id}",
            "title": _PROACTIVE_INBOX_TITLE,
            "turns": [],
            "is_proactive_inbox": True,
            "created_at": ts,
            "updated_at": ts,
        })
        .execute()
    )
    return insert_res.data[0]["id"]


_TIME_BOUNDED_JOB_TYPES = {"morning_brief", "follow_up_scan"}


def _fetch_undelivered(user_id: str, seen_ids: set[str]) -> list[dict]:
    db = get_admin_db()
    res = (
        db.table("proactive_jobs")
        .select("id, job_type, result, status, delivered, started_at, finished_at")
        .eq("user_id", user_id)
        .eq("status", "done")
        .eq("delivered", False)
        .order("started_at", desc=False)
        .execute()
    )
    rows = [r for r in (res.data or []) if r["id"] not in seen_ids]

    # For time-bounded types keep only newest; silently discard older stale instances.
    stale_ids: list[str] = []
    kept: list[dict] = []
    seen_types: set[str] = set()
    for row in reversed(rows):
        jt = row["job_type"]
        if jt in _TIME_BOUNDED_JOB_TYPES:
            if jt not in seen_types:
                seen_types.add(jt)
                kept.append(row)
            else:
                stale_ids.append(row["id"])
        else:
            kept.append(row)

    if stale_ids:
        db.table("proactive_jobs").update({"delivered": True}).in_("id", stale_ids).execute()

    return sorted(kept, key=lambda r: r["started_at"])


@router.get("/agent/proactive/init")
async def proactive_init(user: dict = Depends(get_current_user)):
    """
    Startup/reconnect handshake for Realtime delivery. Returns the proactive inbox
    chat id and the current undelivered backlog (rows generated while the client
    was offline — Realtime only pushes changes that occur after subscribe), and
    bumps last_checkin_at so the inactivity guard keeps generating for this user.
    Idempotent and cheap, so the client also calls it as a periodic checkin.
    """
    user_id: str = user["sub"]

    chat_id = await asyncio.to_thread(_get_or_create_proactive_inbox, user_id)

    # Mark user active so the cron inactivity guard doesn't skip future jobs.
    try:
        await asyncio.to_thread(
            lambda: get_admin_db()
                .table("users")
                .update({"last_checkin_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", user_id)
                .execute()
        )
    except Exception as exc:
        print(f"[proactive_init] checkin bump failed: {exc}")

    # Drain the backlog (also marks stale duplicate time-bounded instances delivered).
    jobs = await asyncio.to_thread(_fetch_undelivered, user_id, set())

    return {"proactive_chat_id": chat_id, "jobs": jobs}


@router.get("/agent/proactive/announce-audio")
async def proactive_announce_audio(user: dict = Depends(get_current_user)):
    """TTS for the morning-brief announcement. Fetched on demand by the client
    (only after its freshness guard passes) since the Realtime row carries no audio."""
    try:
        audio_b64 = await synthesize("Your morning brief is ready.")
    except Exception as exc:
        print(f"[proactive_announce_audio] TTS failed: {exc}")
        audio_b64 = None
    return {"audio_b64": audio_b64}


@router.get("/agent/proactive/nudge-audio")
async def proactive_nudge_audio(n: int = 1, user: dict = Depends(get_current_user)):
    """TTS for the quiet-clear nudge. Spoken once the user's call/meeting ends to
    flag alerts that were carded instead of spoken while they were unavailable."""
    n = max(1, min(99, n))
    text = "You have a notification." if n == 1 else f"You have {n} notifications."
    try:
        audio_b64 = await synthesize(text)
    except Exception as exc:
        print(f"[proactive_nudge_audio] TTS failed: {exc}")
        audio_b64 = None
    return {"audio_b64": audio_b64}


class AckBody(BaseModel):
    id: str


@router.post("/agent/proactive/ack")
async def proactive_ack(body: AckBody, user: dict = Depends(get_current_user)):
    db = get_admin_db()
    db.table("proactive_jobs").update({"delivered": True}).eq("id", body.id).eq("user_id", user["sub"]).execute()
    return {"ok": True}


class TriggerBody(BaseModel):
    job_type: str
    context_key: str | None = None


@router.post("/agent/proactive/trigger")
async def proactive_trigger(body: TriggerBody, user: dict = Depends(get_current_user)):
    db = get_admin_db()
    tz_res = db.table("users").select("timezone").eq("id", user["sub"]).maybe_single().execute()
    user_tz = (tz_res.data or {}).get("timezone") or "UTC"
    try:
        result = await run_job(user["sub"], body.job_type, body.context_key, user_tz=user_tz)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Job failed terminally: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=409, detail="Job skipped (dedupe, locked, or already failed)")
    return {
        "ok": True,
        "text": result.text,
        "email_cards": result.email_cards,
        "calendar_cards": result.calendar_cards,
        "task_cards": result.task_cards,
    }


@router.get("/debug/patterns")
async def debug_patterns(user: dict = Depends(get_current_user)):
    db = get_admin_db()
    res = (
        db.table("user_behavior_patterns")
        .select("id, pattern_type, query_template, frequency, auto_run, confidence, last_seen_at, first_seen_at")
        .eq("user_id", user["sub"])
        .order("frequency", desc=True)
        .execute()
    )
    return {"patterns": res.data or []}


@router.get("/profile/learned")
async def profile_learned(user: dict = Depends(get_current_user)):
    """Aggregate for the 'What I've learned' panel: auto-brief intent labels (no counts),
    learned habits (recurring reminders), and the suggestion accept/dismiss outcome.
    Admin client bypasses RLS, so every query is scoped by user_id explicitly."""
    db = get_admin_db()
    uid = user["sub"]

    auto_res = (
        db.table("user_behavior_patterns")
        .select("query_template")
        .eq("user_id", uid)
        .eq("auto_run", True)
        .execute()
    )
    auto_brief = [r["query_template"] for r in (auto_res.data or []) if r.get("query_template")]

    habit_res = (
        db.table("recall_items")
        .select("id, content, recurrence, created_at")
        .eq("user_id", uid)
        .eq("status", "open")
        .not_.is_("recurrence", "null")
        .order("created_at", desc=True)
        .execute()
    )
    habits = [
        {"id": r["id"], "content": r["content"], "recurrence": r.get("recurrence")}
        for r in (habit_res.data or [])
    ]

    sug_res = (
        db.table("agent_suggestions")
        .select("status")
        .eq("user_id", uid)
        .execute()
    )
    counts = {"accepted": 0, "dismissed": 0, "pending": 0}
    for r in (sug_res.data or []):
        s = r.get("status")
        if s in counts:
            counts[s] += 1
    counts["total"] = sum(counts.values())

    # Separate query, unrelated to the pattern-learning data above: connection health.
    user_res = (
        db.table("users")
        .select("google_reauth_required")
        .eq("id", uid)
        .maybe_single()
        .execute()
    )
    google_reauth_required = bool((user_res.data or {}).get("google_reauth_required"))

    return {
        "auto_brief": auto_brief,
        "habits": habits,
        "suggestions": counts,
        "google_reauth_required": google_reauth_required,
    }

"""
Proactive agent delivery routes.

GET  /agent/proactive/stream   — SSE stream of undelivered proactive job results
POST /agent/proactive/ack      — Mark a job as delivered (body: {id: str})
POST /agent/proactive/trigger  — Manually trigger a job (body: {job_type: str, context_key?: str})
"""
import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth import get_current_user
from db import get_admin_db
from proactive.runner import run_job
from tts import synthesize

router = APIRouter()

_PROACTIVE_INBOX_TITLE = "Recall"
_POLL_INTERVAL_S = 30
_HEARTBEAT_S = 25


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


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
        .select("id, job_type, result, started_at, finished_at")
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


@router.get("/agent/proactive/stream")
async def proactive_stream(
    request: Request,
    user: dict = Depends(get_current_user),
):
    user_id: str = user["sub"]

    async def event_gen():
        # Create proactive inbox chat if needed, send chat_id so frontend can locate it
        try:
            chat_id = await asyncio.to_thread(_get_or_create_proactive_inbox, user_id)
        except Exception as exc:
            yield _sse({"type": "error", "message": f"Inbox init failed: {exc}"})
            return

        yield _sse({"type": "connected", "proactive_chat_id": chat_id})

        # Fire-and-forget: mark user active so inactivity guard doesn't skip future crons.
        asyncio.create_task(
            asyncio.to_thread(
                lambda: get_admin_db()
                    .table("users")
                    .update({"last_checkin_at": datetime.now(timezone.utc).isoformat()})
                    .eq("id", user_id)
                    .execute()
            )
        )

        seen_ids: set[str] = set()
        last_poll = 0.0

        while True:
            if await request.is_disconnected():
                break

            now = asyncio.get_event_loop().time()
            if now - last_poll >= _POLL_INTERVAL_S:
                try:
                    jobs = await asyncio.to_thread(_fetch_undelivered, user_id, seen_ids)
                    for job in jobs:
                        seen_ids.add(job["id"])
                        audio_b64 = None
                        if job["job_type"] == "morning_brief":
                            try:
                                audio_b64 = await synthesize("Your morning brief is ready.")
                            except Exception as tts_exc:
                                print(f"[proactive_stream] TTS failed: {tts_exc}")
                        yield _sse({
                            "type": "proactive_job",
                            "id": job["id"],
                            "job_type": job["job_type"],
                            "result": job["result"] or {},
                            "audio_b64": audio_b64,
                            "proactive_chat_id": chat_id,
                            "timestamp": job.get("finished_at") or job["started_at"],
                            "started_at": job["started_at"],
                            "finished_at": job.get("finished_at"),
                        })
                except Exception:
                    pass
                last_poll = now

            yield _sse({"type": "heartbeat"})
            await asyncio.sleep(_HEARTBEAT_S)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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

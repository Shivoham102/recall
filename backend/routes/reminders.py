from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from db import get_db
from tts import synthesize
from auth import get_current_user
from time_utils import next_occurrence

router = APIRouter()


class DismissRequest(BaseModel):
    ids: list[str]


@router.post("/reminders/dismiss")
def dismiss_reminders(body: DismissRequest, user: dict = Depends(get_current_user)):
    """Mark a list of reminder items as reminded without delivering audio. Used for missed reminders on startup."""
    if not body.ids:
        return {"dismissed": 0}
    now = datetime.now(timezone.utc).isoformat()
    for item_id in body.ids:
        (
            get_db()
            .table("recall_items")
            .update({"reminded_at": now})
            .eq("id", item_id)
            .eq("user_id", user["sub"])
            .execute()
        )
    return {"dismissed": len(body.ids)}


@router.get("/reminders/pending")
def get_pending_reminders(user: dict = Depends(get_current_user)):
    """Read-only — returns all unreminded future items. No side effects."""
    result = (
        get_db()
        .table("recall_items")
        .select("id, content, intent_type, due_at")
        .eq("user_id", user["sub"])
        .not_.is_("due_at", "null")
        .is_("reminded_at", "null")
        .eq("status", "open")
        .execute()
    )
    return result.data or []


@router.get("/reminders/due")
async def get_due_reminders(user: dict = Depends(get_current_user)):
    """Returns all currently-due items with TTS audio, marking each reminded only after success."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_db()
        .table("recall_items")
        .select("id, content, intent_type, reminder_text, recurrence")
        .eq("user_id", user["sub"])
        .lte("due_at", now)
        .is_("reminded_at", "null")
        .eq("status", "open")
        .execute()
    )
    items = result.data or []
    if not items:
        return []

    output = []
    for item in items:
        try:
            spoken = item.get("reminder_text") or f"Reminder: {item['content']}"
            audio = await synthesize(spoken)
        except Exception:
            continue  # leave reminded_at null so it retries next call
        # Recurring reminders: fire once (late if overdue), then roll due_at forward to the
        # next future occurrence and leave reminded_at null so they fire again. One-off
        # reminders: mark reminded so they don't repeat.
        recurrence = item.get("recurrence")
        if recurrence:
            try:
                next_due = next_occurrence(recurrence, datetime.now(timezone.utc))
                get_db().table("recall_items").update({"due_at": next_due}).eq("id", item["id"]).execute()
            except Exception:
                # Malformed recurrence — mark reminded to avoid a fire loop
                get_db().table("recall_items").update({"reminded_at": now}).eq("id", item["id"]).execute()
        else:
            get_db().table("recall_items").update({"reminded_at": now}).eq("id", item["id"]).execute()
        output.append({
            "id": item["id"],
            "content": item["content"],
            "intent_type": item["intent_type"],
            "audio_base64": audio,
        })
    return output


@router.post("/reminders/mark-missed")
def mark_missed(user: dict = Depends(get_current_user)):
    """Find all open items past due by >2h and mark them status=missed + reminded_at=now.
    Returns the marked items so the client can surface the banner."""
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(hours=2)).isoformat()
    now = now_dt.isoformat()
    result = (
        get_db()
        .table("recall_items")
        .select("id, content")
        .eq("user_id", user["sub"])
        .eq("status", "open")
        .is_("reminded_at", "null")
        .is_("recurrence", "null")  # recurring reminders never go "missed" — /reminders/due owns them
        .lte("due_at", cutoff)
        .execute()
    )
    items = result.data or []
    for item in items:
        (
            get_db()
            .table("recall_items")
            .update({"status": "missed", "reminded_at": now})
            .eq("id", item["id"])
            .eq("user_id", user["sub"])
            .execute()
        )
    return {"items": items}


def recent_fired_reminders(user_id: str, window_min: int = 30, limit: int = 3) -> list[dict]:
    """One-off reminders that fired (or were marked missed) within the last
    `window_min` minutes, most recent first. Lets a bare snooze ("give me 15 more
    minutes", with no task named) resolve to the reminder that just went off.
    Recurring items are excluded — they keep reminded_at null and roll forward."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).isoformat()
    result = (
        get_db()
        .table("recall_items")
        .select("id, content, reminded_at")
        .eq("user_id", user_id)
        .is_("recurrence", "null")
        .not_.is_("reminded_at", "null")
        .gte("reminded_at", cutoff)
        .in_("status", ["open", "missed"])
        .order("reminded_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []

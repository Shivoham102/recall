from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from db import get_db
from tts import synthesize

router = APIRouter()


class DismissRequest(BaseModel):
    ids: list[str]


@router.post("/reminders/dismiss")
def dismiss_reminders(body: DismissRequest):
    """Mark a list of reminder items as reminded without delivering audio. Used for missed reminders on startup."""
    if not body.ids:
        return {"dismissed": 0}
    now = datetime.now(timezone.utc).isoformat()
    for item_id in body.ids:
        get_db().table("recall_items").update({"reminded_at": now}).eq("id", item_id).execute()
    return {"dismissed": len(body.ids)}


@router.get("/reminders/pending")
def get_pending_reminders():
    """Read-only — returns all unreminded future items. No side effects."""
    result = (
        get_db()
        .table("recall_items")
        .select("id, content, intent_type, due_at")
        .not_.is_("due_at", "null")
        .is_("reminded_at", "null")
        .neq("status", "done")
        .execute()
    )
    return result.data or []


@router.get("/reminders/due")
async def get_due_reminders():
    """Returns all currently-due items with TTS audio, marking each reminded only after success."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_db()
        .table("recall_items")
        .select("id, content, intent_type, reminder_text")
        .lte("due_at", now)
        .is_("reminded_at", "null")
        .neq("status", "done")
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
        # Mark reminded only after TTS succeeds
        get_db().table("recall_items").update({"reminded_at": now}).eq("id", item["id"]).execute()
        output.append({
            "id": item["id"],
            "content": item["content"],
            "intent_type": item["intent_type"],
            "audio_base64": audio,
        })
    return output

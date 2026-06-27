import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from db import get_db
from tts import synthesize
from auth import get_current_user
from time_utils import next_occurrence

logger = logging.getLogger(__name__)
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
async def get_due_reminders(silent: bool = False, user: dict = Depends(get_current_user)):
    """Returns all currently-due items, marking each reminded / rolling recurrence
    forward. Normally includes TTS audio; with silent=1 (client is on a call and
    will show a card instead) it skips synthesis and returns empty audio."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_db()
        .table("recall_items")
        .select("id, content, intent_type, reminder_text, recurrence, due_at")
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
        # Claim the fire with a compare-and-set BEFORE synthesizing TTS. Two concurrent
        # /reminders/due calls both pass the SELECT (reminded_at still null / due_at still
        # past); the CAS lets exactly one win so we never fire — or burn TTS — twice.
        # Recurring: roll due_at forward only if it still equals the value we read.
        # One-off / malformed recurrence: set reminded_at only if still null.
        recurrence = item.get("recurrence")
        if recurrence:
            try:
                next_due = next_occurrence(recurrence, datetime.now(timezone.utc))
                claim = (
                    get_db().table("recall_items")
                    .update({"due_at": next_due})
                    .eq("id", item["id"])
                    .eq("due_at", item["due_at"])
                    .execute()
                )
            except Exception:
                # Malformed recurrence rule — can't roll forward. Mark reminded so it fires
                # once and stops (leaving reminded_at null would re-fire every poll). Log it
                # instead of swallowing so a broken rule is diagnosable, not silently lost.
                logger.warning(
                    "reminders.due: malformed recurrence for item %s (user %s), firing once and stopping: %r",
                    item["id"], user["sub"], item.get("recurrence"),
                )
                claim = (
                    get_db().table("recall_items")
                    .update({"reminded_at": now})
                    .eq("id", item["id"])
                    .is_("reminded_at", "null")
                    .execute()
                )
        else:
            claim = (
                get_db().table("recall_items")
                .update({"reminded_at": now})
                .eq("id", item["id"])
                .is_("reminded_at", "null")
                .execute()
            )
        if not (claim.data or []):
            continue  # another concurrent caller already fired this one — skip it

        # We own this fire. Now (and only now) pay for TTS.
        if silent:
            audio = ""  # client will card it — don't burn TTS on audio it discards
        else:
            spoken = item.get("reminder_text") or f"Reminder: {item['content']}"
            try:
                audio = await synthesize(spoken)
            except Exception:
                audio = ""  # TTS unavailable (e.g. quota) — still fire, just silent
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

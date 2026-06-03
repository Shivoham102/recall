from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
import re
from db import get_db
from auth import get_current_user
from time_utils import parse_due_at, is_valid_iana

router = APIRouter()

_PREFIX_PATTERNS = (
    r"^\s*please\s+remind\s+me\s+to\s+",
    r"^\s*remind\s+me\s+to\s+",
    r"^\s*remind\s+me\s+",
    r"^\s*set\s+(?:me\s+)?(?:a\s+)?reminder\s+to\s+",
    r"^\s*set\s+(?:me\s+)?(?:a\s+)?reminder\s+for\s+",
)

_TEMPORAL_PATTERNS = (
    r"\b(today|tomorrow|tonight)\b",
    r"\bthis\s+(morning|afternoon|evening|night)\b",
    r"\b(?:at|by)\s+\d{1,2}(?::\d{2})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?)?\b",
    r"\b(?:next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
)

_DUE_TIME_RE = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?))\b", re.IGNORECASE)


def _extract_due_time(due_hint: str | None) -> str | None:
    if not due_hint:
        return None
    match = _DUE_TIME_RE.search(due_hint)
    if match:
        cleaned = re.sub(r"\s+", " ", match.group(1)).strip().upper().replace(".", "")
        return cleaned
    if re.search(r"\bnoon\b", due_hint, re.IGNORECASE):
        return "NOON"
    if re.search(r"\bmidnight\b", due_hint, re.IGNORECASE):
        return "MIDNIGHT"
    return None


def _build_display_text(content: str, due_hint: str | None) -> str:
    text = re.sub(r"\s+", " ", content.strip())
    if not text:
        return ""

    for pattern in _PREFIX_PATTERNS:
        text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)

    for pattern in _TEMPORAL_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).strip(" .,!;:-")
    if not text:
        text = re.sub(r"\s+", " ", content.strip()).strip(" .,!;:-")

    due_time = _extract_due_time(due_hint)
    if due_time and due_time.lower() not in text.lower():
        text = f"{text} at {due_time}"

    return text


@router.get("/items")
def get_items(
    user: dict = Depends(get_current_user),
    status: str | None = None,
    has_due_hint: bool | None = None,
    limit: int = Query(100, le=500),
):
    db = get_db()
    query = db.table("recall_items").select(
        "id,content,intent_type,status,created_at,updated_at,due_hint,due_at,recurrence,reminded_at"
    ).eq("user_id", user["sub"])
    if status:
        query = query.eq("status", status)
    if has_due_hint is True:
        query = query.not_.is_("due_hint", "null")
    elif has_due_hint is False:
        query = query.is_("due_hint", "null")
    result = query.order("created_at", desc=True).limit(limit).execute()
    data = result.data or []
    for item in data:
        item["display_text"] = _build_display_text(
            item.get("content", ""),
            item.get("due_hint"),
        )
    return data


class ItemUpdate(BaseModel):
    status: str | None = None
    due_hint: str | None = None
    # Explicit new due time (ISO 8601). Used by snooze/reschedule, which shift the EXISTING
    # due by a delta client-side, so no NLP parsing or tz is needed.
    due_at: str | None = None
    # recurrence: pass {} or null to CLEAR (stop repeating); a dict to set. Sentinel below
    # distinguishes "field absent" from "explicitly clearing", since None means clear here.
    recurrence: dict | None = None
    clear_recurrence: bool = False
    # Client-supplied IANA tz for parsing a new due_hint (legacy/voice path). Trusted like
    # capture_stream's timezone; never written to the row. Falls back to UTC if invalid.
    timezone: str | None = None


@router.patch("/items/{item_id}")
def update_item(item_id: str, body: ItemUpdate, user: dict = Depends(get_current_user)):
    db = get_db()
    update = {
        k: v
        for k, v in body.model_dump(exclude={"recurrence", "clear_recurrence", "timezone"}).items()
        if v is not None
    }
    if body.clear_recurrence:
        update["recurrence"] = None
        update["due_hint"] = None
        update["due_at"] = None
    elif body.recurrence is not None:
        update["recurrence"] = body.recurrence

    # A new due_hint (legacy/voice path) is parsed to due_at in the user's tz.
    if body.due_hint is not None and not body.clear_recurrence:
        tz = body.timezone if (body.timezone and is_valid_iana(body.timezone)) else "UTC"
        due_at = parse_due_at(body.due_hint, tz)
        if not due_at:
            raise HTTPException(status_code=422, detail=f"Could not parse due time: {body.due_hint}")
        update["due_at"] = due_at

    # Whenever due_at moved to a concrete time (explicit snooze or parsed hint), clear
    # reminded_at and reopen so a fired/missed reminder can fire again. Mirrors tools/memory.py.
    if update.get("due_at") is not None and not body.clear_recurrence:
        update["reminded_at"] = None
        update.setdefault("status", "open")

    if not update:
        return {}
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = (
        db.table("recall_items")
        .update(update)
        .eq("id", item_id)
        .eq("user_id", user["sub"])
        .execute()
    )
    return result.data[0] if result.data else {}


@router.delete("/items/{item_id}")
def delete_item(item_id: str, user: dict = Depends(get_current_user)):
    """Hard-delete a recall_item. Unlike resolving (status change, which stays a learning
    signal for pattern_learn/goal_neglect), deletion removes the row entirely so a
    mis-captured item leaves no trace. Scoped by user_id; 404 if nothing matched."""
    db = get_db()
    result = (
        db.table("recall_items")
        .delete()
        .eq("id", item_id)
        .eq("user_id", user["sub"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}

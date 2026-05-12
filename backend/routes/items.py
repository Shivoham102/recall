from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
import re
from db import get_db
from auth import get_current_user

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
    has_due_hint: bool = False,
    limit: int = Query(100, le=500),
):
    db = get_db()
    query = db.table("recall_items").select(
        "id,content,intent_type,status,created_at,updated_at,due_hint,due_at"
    ).eq("user_id", user["sub"])
    if status:
        query = query.eq("status", status)
    if has_due_hint:
        query = query.not_.is_("due_hint", "null")
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


@router.patch("/items/{item_id}")
def update_item(item_id: str, body: ItemUpdate, user: dict = Depends(get_current_user)):
    db = get_db()
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        return {}
    result = (
        db.table("recall_items")
        .update(update)
        .eq("id", item_id)
        .eq("user_id", user["sub"])
        .execute()
    )
    return result.data[0] if result.data else {}

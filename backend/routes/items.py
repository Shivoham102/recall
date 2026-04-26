from fastapi import APIRouter, Query
from pydantic import BaseModel
from db import get_db

router = APIRouter()


@router.get("/items")
def get_items(
    status: str | None = None,
    has_due_hint: bool = False,
    limit: int = Query(100, le=500),
):
    db = get_db()
    query = db.table("recall_items").select(
        "id,content,intent_type,status,created_at,updated_at,due_hint"
    )
    if status:
        query = query.eq("status", status)
    if has_due_hint:
        query = query.not_.is_("due_hint", "null")
    result = query.order("created_at", desc=True).limit(limit).execute()
    return result.data


class ItemUpdate(BaseModel):
    status: str | None = None
    due_hint: str | None = None


@router.patch("/items/{item_id}")
def update_item(item_id: str, body: ItemUpdate):
    db = get_db()
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        return {}
    result = db.table("recall_items").update(update).eq("id", item_id).execute()
    return result.data[0] if result.data else {}

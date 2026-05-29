"""
Agent suggestions delivery + actions.

GET  /agent/suggestions          — pending suggestions for the user
POST /agent/suggestions/{id}/accept
POST /agent/suggestions/{id}/dismiss

Accept creates a recall_item via store_item, which derives user_id from the
current_user_id ContextVar — no middleware sets it on plain endpoints, so we set it
here (else the new row gets user_id=NULL).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

import context
from auth import get_current_user
from db import get_admin_db
from rag import store_item

router = APIRouter()


@router.get("/agent/suggestions")
def list_suggestions(user: dict = Depends(get_current_user)):
    db = get_admin_db()
    res = (
        db.table("agent_suggestions")
        .select("id, kind, title, payload, created_at")
        .eq("user_id", user["sub"])
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    return {"suggestions": res.data or []}


def _get_pending(db, suggestion_id: str, user_id: str) -> dict:
    res = (
        db.table("agent_suggestions")
        .select("id, kind, title, payload, status")
        .eq("id", suggestion_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Suggestion already {row['status']}")
    return row


@router.post("/agent/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    db = get_admin_db()
    user_id = user["sub"]
    row = _get_pending(db, suggestion_id, user_id)
    kind = row["kind"]
    payload = row.get("payload") or {}
    now = datetime.now(timezone.utc)

    # store_item reads user_id from this ContextVar — required or the row is orphaned.
    tok = context.current_user_id.set(user_id)
    try:
        if kind == "recurring_reminder":
            recurrence = payload.get("recurrence")
            content = payload.get("content") or row.get("title") or "Reminder"
            if not recurrence:
                raise HTTPException(status_code=400, detail="Suggestion missing recurrence")
            store_item(content=content, intent_type="task", recurrence=recurrence)
            source_ids = payload.get("source_item_ids") or []
            if source_ids:
                (
                    db.table("recall_items")
                    .update({"status": "resolved", "updated_at": now.isoformat()})
                    .in_("id", source_ids)
                    .eq("user_id", user_id)
                    .execute()
                )
        elif kind == "neglected_goal":
            goal_text = payload.get("goal_text") or row.get("title") or "Goal"
            soon = (now + timedelta(hours=2)).isoformat()
            store_item(content=goal_text, intent_type="task", due_hint="in 2 hours", due_at=soon)
            goal_id = payload.get("goal_id")
            if goal_id:
                (
                    db.table("user_goals")
                    .update({"last_acted_at": now.isoformat()})
                    .eq("id", goal_id)
                    .eq("user_id", user_id)
                    .execute()
                )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown suggestion kind: {kind}")
    finally:
        context.current_user_id.reset(tok)

    (
        db.table("agent_suggestions")
        .update({"status": "accepted", "acted_at": now.isoformat()})
        .eq("id", suggestion_id)
        .eq("user_id", user_id)
        .execute()
    )
    return {"ok": True}


@router.post("/agent/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    db = get_admin_db()
    user_id = user["sub"]
    row = _get_pending(db, suggestion_id, user_id)
    now = datetime.now(timezone.utc).isoformat()

    # For neglected goals, bump last_surfaced_at so it won't nag again this cadence.
    if row["kind"] == "neglected_goal":
        goal_id = (row.get("payload") or {}).get("goal_id")
        if goal_id:
            (
                db.table("user_goals")
                .update({"last_surfaced_at": now})
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

    (
        db.table("agent_suggestions")
        .update({"status": "dismissed", "acted_at": now})
        .eq("id", suggestion_id)
        .eq("user_id", user_id)
        .execute()
    )
    return {"ok": True}

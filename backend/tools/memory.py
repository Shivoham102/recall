import asyncio
from datetime import datetime, timezone
import context
from db import get_db
from rag import embed, retrieve_similar
from time_utils import parse_due_at, next_occurrence, recurrence_due_hint, is_valid_iana

async def classify_intent(inp: dict) -> dict:
    # No-op — the agent's input IS the classification. We just acknowledge it.
    return {"status": "classified", "summary": f"Intent: {inp.get('intent_type', 'note')}"}


async def recall_search(inp: dict) -> dict:
    query = inp["query"]
    limit = int(inp.get("limit", 10))
    status_filter = inp.get("status", "open")

    items = await asyncio.to_thread(retrieve_similar, query, limit)

    if status_filter != "all":
        items = [i for i in items if i.get("status") == status_filter]

    # Per-request scratch (see context.py) so surface_tasks can resolve indices to rows
    # without leaking one user's results into another's request on a warm instance.
    context.current_task_fetch.set([
        {
            "id": i["id"],
            "content": i["content"],
            "intent_type": i["intent_type"],
            "status": i["status"],
            "created_at": i["created_at"],
            "due_hint": i.get("due_hint"),
            "due_at": i.get("due_at"),
            "recurrence": i.get("recurrence"),
        }
        for i in items
    ])

    # Include indices so the agent can reference specific tasks when calling surface_tasks
    formatted = [
        f"[{idx}] [{i['intent_type']}] {i['content']} (status: {i['status']}, created: {i['created_at'][:10]}, id: {i['id']})"
        for idx, i in enumerate(items)
    ]
    return {
        "summary": f"Found {len(items)} item(s)",
        "items": formatted,
    }


async def surface_tasks(inp: dict) -> dict:
    """Display specific tasks as visual cards in the UI.
    The agent calls this with the indices of tasks it is about to mention."""
    indices = inp.get("indices", [])
    cache = context.current_task_fetch.get([])
    selected = [cache[i] for i in indices if i < len(cache)]
    return {
        "summary": f"Showing {len(selected)} task(s)",
        "card_type": "tasks",
        "items_data": selected,
    }


async def recall_update_item(inp: dict) -> dict:
    item_id = inp["item_id"]
    user_id = context.current_user_id.get("")
    if not user_id:
        return {
            "summary": "Cannot update item without a user scope",
            "updated": False,
            "item_id": item_id,
            "error": True,
        }

    update: dict = {}

    if "status" in inp and inp.get("status") is not None:
        update["status"] = inp["status"]

    if "content" in inp and inp.get("content") is not None:
        content = str(inp["content"]).strip()
        if content:
            update["content"] = content
            update["embedding"] = embed(content)

    if "reminder_text" in inp:
        update["reminder_text"] = inp.get("reminder_text")

    if "recurrence" in inp:
        recurrence = inp.get("recurrence")
        if recurrence is None:
            update["recurrence"] = None  # stop repeating; due_hint/due_at left as-is unless also cleared
        else:
            try:
                tz_val = recurrence.get("tz") or ""
                if not is_valid_iana(tz_val):
                    recurrence = {**recurrence, "tz": context.current_user_tz.get("UTC")}
                next_due = next_occurrence(recurrence, datetime.now(timezone.utc))
            except Exception:
                return {
                    "summary": f"Invalid recurrence: {recurrence}",
                    "updated": False,
                    "item_id": item_id,
                    "error": True,
                }
            update["recurrence"] = recurrence
            update["due_at"] = next_due
            # Keep due_hint non-null so the item stays classified as a reminder.
            if "due_hint" not in inp:
                update["due_hint"] = recurrence_due_hint(recurrence)

    if "due_hint" in inp:
        due_hint = inp.get("due_hint")
        if due_hint is None:
            update["due_hint"] = None
            update["due_at"] = None
        else:
            due_hint_text = str(due_hint).strip()
            due_at = parse_due_at(due_hint_text, context.current_user_tz.get("UTC"))
            if not due_at:
                return {
                    "summary": f"Could not parse due date: {due_hint}",
                    "updated": False,
                    "item_id": item_id,
                    "error": True,
                }
            update["due_hint"] = due_hint_text
            update["due_at"] = due_at

    # Rescheduling moves due_at to a new time. A one-off that already fired/was
    # missed has reminded_at set (and maybe status="missed"), which hides it from
    # /reminders/due (needs reminded_at null AND status "open"). Clear the flag and
    # reopen so it can fire again. Recurring items already keep reminded_at null, so
    # this is a no-op for them. due_hint=None clears due_at, so this branch is skipped.
    if update.get("due_at") is not None:
        update["reminded_at"] = None
        update.setdefault("status", "open")  # don't override an explicit status in this turn

    if not update:
        return {
            "summary": "No item updates provided",
            "updated": False,
            "item_id": item_id,
        }

    update["updated_at"] = datetime.now(timezone.utc).isoformat()

    result = await asyncio.to_thread(
        lambda: get_db()
            .table("recall_items")
            .update(update)
            .eq("id", item_id)
            .eq("user_id", user_id)
            .execute()
    )
    rows = result.data or []
    if not rows:
        return {
            "summary": "No matching item found to update",
            "updated": False,
            "item_id": item_id,
            "error": True,
        }

    row = rows[0]

    return {
        "summary": "Updated item",
        "updated": True,
        "item_id": item_id,
        "due_at": row.get("due_at"),
        "due_hint": row.get("due_hint"),
        "status": row.get("status"),
    }

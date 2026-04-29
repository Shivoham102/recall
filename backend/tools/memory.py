import asyncio
from db import get_db
from rag import retrieve_similar

# Module-level cache so surface_tasks can look up by index (single-user app)
_last_task_fetch: list = []


async def classify_intent(inp: dict) -> dict:
    # No-op — the agent's input IS the classification. We just acknowledge it.
    return {"status": "classified", "summary": f"Intent: {inp.get('intent_type', 'note')}"}


async def recall_search(inp: dict) -> dict:
    global _last_task_fetch

    query = inp["query"]
    limit = int(inp.get("limit", 10))
    status_filter = inp.get("status", "open")

    items = await asyncio.to_thread(retrieve_similar, query, limit)

    if status_filter != "all":
        items = [i for i in items if i.get("status") == status_filter]

    _last_task_fetch = [
        {
            "id": i["id"],
            "content": i["content"],
            "intent_type": i["intent_type"],
            "status": i["status"],
            "created_at": i["created_at"],
            "due_hint": i.get("due_hint"),
        }
        for i in items
    ]

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
    selected = [_last_task_fetch[i] for i in indices if i < len(_last_task_fetch)]
    return {
        "summary": f"Showing {len(selected)} task(s)",
        "card_type": "tasks",
        "items_data": selected,
    }


async def recall_update_item(inp: dict) -> dict:
    item_id = inp["item_id"]
    status = inp.get("status", "resolved")
    due_hint = inp.get("due_hint")

    update: dict = {"status": status}
    if due_hint:
        update["due_hint"] = due_hint

    await asyncio.to_thread(
        lambda: get_db().table("recall_items").update(update).eq("id", item_id).execute()
    )

    return {
        "summary": f"Marked item as {status}",
        "updated": True,
        "item_id": item_id,
    }

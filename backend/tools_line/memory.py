import os
import uuid
import asyncio
from typing import Annotated
from supabase import create_client, Client
from openai import OpenAI
from line.llm_agent import loopback_tool

_service_client: Client | None = None
_openai_client: OpenAI | None = None


def _service_db() -> Client:
    global _service_client
    if _service_client is None:
        _service_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _service_client


def _openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


def _embed(text: str) -> list[float]:
    resp = _openai().embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding


def _retrieve_similar(text: str, user_id: str, match_count: int = 10) -> list[dict]:
    vec = _embed(text)
    result = _service_db().rpc(
        "match_recall_items",
        {"query_embedding": vec, "match_count": match_count, "p_user_id": user_id},
    ).execute()
    return result.data or []


def make_classify_intent(user_id: str):
    @loopback_tool
    async def classify_intent(
        ctx,
        intent_type: Annotated[str, "task|blocker|follow_up|progress|note|query|update"],
        should_store: Annotated[bool, "Whether to persist this item"],
        due_hint: Annotated[str, "Natural language due date, omit if none"] = "",
        reminder_text: Annotated[str, "Reminder text, omit if none"] = "",
    ):
        """Classify the intent of the user's message. Call on every turn with a spoken response."""
        return {"status": "classified", "summary": f"Intent: {intent_type}"}

    return classify_intent


def make_recall_search(user_id: str):
    last_fetch: list = []

    @loopback_tool
    async def recall_search(
        ctx,
        query: Annotated[str, "Semantic search query"],
        limit: Annotated[int, "Max results, default 10"] = 10,
        status: Annotated[str, "open/resolved/all, default open"] = "open",
    ):
        """Semantically search the user's stored recall items. Call surface_tasks after to display results."""
        items = await asyncio.to_thread(_retrieve_similar, query, user_id, limit)

        if status != "all":
            items = [i for i in items if i.get("status") == status]

        last_fetch.clear()
        last_fetch.extend({
            "id": i["id"],
            "content": i["content"],
            "intent_type": i["intent_type"],
            "status": i["status"],
            "created_at": i["created_at"],
            "due_hint": i.get("due_hint"),
        } for i in items)

        formatted = [
            f"[{idx}] [{i['intent_type']}] {i['content']} "
            f"(status: {i['status']}, created: {i['created_at'][:10]}, id: {i['id']})"
            for idx, i in enumerate(items)
        ]
        return {"summary": f"Found {len(items)} item(s)", "items": formatted}

    # Expose last_fetch so surface_tasks can share the same list
    recall_search._last_fetch = last_fetch
    return recall_search


def make_surface_tasks(user_id: str, fetch_ref: list):
    @loopback_tool
    async def surface_tasks(
        ctx,
        indices: Annotated[list[int], "Indices from recall_search to display as cards"],
    ):
        """Display specific tasks as visual cards in the UI. Call after recall_search."""
        selected = [fetch_ref[i] for i in indices if i < len(fetch_ref)]
        idempotency_id = str(uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{user_id}:{getattr(ctx, 'tool_call_id', str(indices))}",
        ))
        await asyncio.to_thread(
            lambda: _service_db().table("ui_events").upsert({
                "id": idempotency_id,
                "user_id": user_id,
                "type": "surface_tasks",
                "payload": {"items": selected},
            }, on_conflict="id").execute()
        )
        return {"summary": f"Showing {len(selected)} task(s)", "count": len(selected)}

    return surface_tasks


def make_recall_update_item(user_id: str):
    @loopback_tool
    async def recall_update_item(
        ctx,
        item_id: Annotated[str, "ID of the recall item to update"],
        status: Annotated[str, "resolved/open/snoozed"],
        due_hint: Annotated[str, "Updated due date in natural language, omit if none"] = "",
    ):
        """Mark a recall item as resolved, snoozed, or update its due date. Only call after user confirmation."""
        update: dict = {"status": status}
        if due_hint:
            update["due_hint"] = due_hint
        await asyncio.to_thread(
            lambda: _service_db().table("recall_items")
                .update(update)
                .eq("id", item_id)
                .eq("user_id", user_id)
                .execute()
        )
        return {"summary": f"Marked item as {status}", "item_id": item_id}

    return recall_update_item

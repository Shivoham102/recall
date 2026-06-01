import os
from datetime import datetime, timezone
from openai import OpenAI
from db import get_db
from time_utils import next_occurrence, recurrence_due_hint, is_valid_iana
import context

_openai: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _openai
    if _openai is None:
        _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai


def embed(text: str) -> list[float]:
    resp = _get_openai().embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding


def retrieve_similar(text: str, match_count: int = 5) -> list[dict]:
    vec = embed(text)
    user_id = context.current_user_id.get("") or None
    result = get_db().rpc(
        "match_recall_items",
        {"query_embedding": vec, "match_count": match_count, "p_user_id": user_id},
    ).execute()
    return result.data or []


def store_item(
    content: str,
    intent_type: str,
    due_hint: str | None = None,
    due_at: str | None = None,
    reminder_text: str | None = None,
    recurrence: dict | None = None,
) -> str:
    vec = embed(content)
    user_id = context.current_user_id.get("") or None
    row: dict = {
        "content": content,
        "embedding": vec,
        "intent_type": intent_type,
        "due_hint": due_hint,
        "status": "open",
        "user_id": user_id,
    }
    # Recurring: due_at is the next occurrence computed from the rule (overrides any passed due_at).
    # Ensure due_hint is non-null so the Tasks/Reminders split treats it as a reminder.
    if recurrence:
        try:
            tz_val = recurrence.get("tz") or ""
            if not is_valid_iana(tz_val):
                recurrence = {**recurrence, "tz": context.current_user_tz.get("UTC")}
            row["recurrence"] = recurrence
            due_at = next_occurrence(recurrence, datetime.now(timezone.utc))
            if not due_hint:
                row["due_hint"] = recurrence_due_hint(recurrence)
        except Exception:
            row.pop("recurrence", None)  # malformed rule → store as a one-off
    if due_at:
        row["due_at"] = due_at
    if reminder_text:
        row["reminder_text"] = reminder_text
    result = get_db().table("recall_items").insert(row).execute()
    return result.data[0]["id"]

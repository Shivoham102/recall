import os
from openai import OpenAI
from dotenv import load_dotenv
from db import get_db
import context

load_dotenv()

_openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def embed(text: str) -> list[float]:
    resp = _openai.embeddings.create(model="text-embedding-3-small", input=text)
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
    if due_at:
        row["due_at"] = due_at
    if reminder_text:
        row["reminder_text"] = reminder_text
    result = get_db().table("recall_items").insert(row).execute()
    return result.data[0]["id"]

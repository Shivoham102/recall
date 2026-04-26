import os
from openai import OpenAI
from dotenv import load_dotenv
from db import get_db

load_dotenv()

_openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def embed(text: str) -> list[float]:
    resp = _openai.embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding


def retrieve_similar(text: str, match_count: int = 5) -> list[dict]:
    vec = embed(text)
    result = get_db().rpc(
        "match_recall_items",
        {"query_embedding": vec, "match_count": match_count},
    ).execute()
    return result.data or []


def store_item(
    content: str,
    intent_type: str,
    due_hint: str | None = None,
) -> str:
    vec = embed(content)
    result = (
        get_db()
        .table("recall_items")
        .insert({
            "content": content,
            "embedding": vec,
            "intent_type": intent_type,
            "due_hint": due_hint,
            "status": "open",
        })
        .execute()
    )
    return result.data[0]["id"]

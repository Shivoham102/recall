from fastapi import APIRouter
from pydantic import BaseModel
from rag import retrieve_similar
from agent import call_agent
from tts import synthesize

router = APIRouter()


class QueryRequest(BaseModel):
    text: str
    session_id: str


def _fmt_context(items: list[dict]) -> str:
    if not items:
        return "No open items found."
    return "\n".join(
        f"- [{i['intent_type']}] {i['content']} "
        f"(status: {i['status']}, created: {i['created_at'][:10]})"
        for i in items
    )


@router.post("/query")
async def query(req: QueryRequest):
    similar = retrieve_similar(req.text)
    rag_context = _fmt_context(similar)
    metadata, spoken = call_agent(req.session_id, req.text, rag_context)
    return {
        "response_text": spoken,
        "audio_base64": await synthesize(spoken),
        "items": similar,
    }

import dateparser
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from stt import transcribe
from rag import retrieve_similar, store_item
from agent import call_agent
from tts import synthesize

router = APIRouter()


def _fmt_context(items: list[dict]) -> str:
    if not items:
        return "No relevant items found."
    return "\n".join(
        f"- [{i['intent_type']}] {i['content']} "
        f"(status: {i['status']}, created: {i['created_at'][:10]})"
        for i in items
    )


def _parse_due_at(due_hint: str | None) -> str | None:
    if not due_hint:
        return None
    from datetime import timezone as _tz
    parsed = dateparser.parse(
        due_hint,
        settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    if not parsed:
        return None
    return parsed.astimezone(_tz.utc).isoformat()


@router.post("/capture")
async def capture(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
):
    audio_bytes = await audio.read()

    try:
        transcript = transcribe(audio_bytes, audio.content_type or "audio/webm")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"STT failed: {e}")

    if not transcript:
        raise HTTPException(status_code=422, detail="Empty transcript")

    similar = retrieve_similar(transcript)
    rag_context = _fmt_context(similar)
    metadata, spoken = call_agent(session_id, transcript, rag_context)

    # Hoist due_at so it can be returned even when should_store is false
    due_at = _parse_due_at(metadata.get("due_hint"))

    item_id = None
    if metadata.get("should_store"):
        item_id = store_item(
            content=transcript,
            intent_type=metadata.get("intent_type", "note"),
            due_hint=metadata.get("due_hint"),
            due_at=due_at,
            reminder_text=metadata.get("reminder_text"),
        )

    return {
        "transcript": transcript,
        "response_text": spoken,
        "audio_base64": await synthesize(spoken),
        "intent_type": metadata.get("intent_type"),
        "item_id": item_id,
        "due_at": due_at,
    }

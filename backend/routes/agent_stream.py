import json
import dateparser
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from stt import transcribe
from rag import retrieve_similar, store_item
from agent import run_agentic_loop
from tts import synthesize

router = APIRouter()


def _fmt_context(items: list[dict]) -> str:
    if not items:
        return "No relevant items found."
    return "\n".join(
        f"- [{i['intent_type']}] {i['content']} "
        f"(status: {i['status']}, created: {i['created_at'][:10]}, id: {i['id']})"
        for i in items
    )


def _parse_due_at(due_hint: str | None) -> str | None:
    if not due_hint:
        return None
    parsed = dateparser.parse(
        due_hint,
        settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    return parsed.isoformat() if parsed else None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/capture/stream")
async def capture_stream(
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

    async def event_stream():
        yield _sse({"type": "transcript", "text": transcript})

        spoken = ""
        metadata: dict = {"intent_type": "note", "should_store": False, "due_hint": None, "reminder_text": None}

        async for event in run_agentic_loop(session_id, transcript, rag_context):
            if event["type"] == "ack":
                # Play acknowledgment audio immediately so the user knows the agent heard them
                try:
                    ack_audio = await synthesize(event["text"])
                    yield _sse({"type": "ack_audio", "audio_base64": ack_audio, "text": event["text"]})
                except Exception:
                    pass
            elif event["type"] == "spoken":
                spoken = event["text"]
            elif event["type"] == "metadata":
                metadata = {k: v for k, v in event.items() if k != "type"}
            else:
                yield _sse(event)

        # Store item if requested
        due_at = _parse_due_at(metadata.get("due_hint"))
        item_id = None
        if metadata.get("should_store") and spoken:
            item_id = store_item(
                content=transcript,
                intent_type=metadata.get("intent_type", "note"),
                due_hint=metadata.get("due_hint"),
                due_at=due_at,
                reminder_text=metadata.get("reminder_text"),
            )

        yield _sse({
            "type": "stored",
            "item_id": item_id,
            "due_at": due_at,
        })

        # Synthesize TTS last
        if spoken:
            try:
                audio_b64 = await synthesize(spoken)
                yield _sse({"type": "audio", "audio_base64": audio_b64})
            except Exception:
                pass

        yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

import json
import dateparser
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from stt import transcribe
from rag import retrieve_similar, store_item
from agent import run_agentic_loop
from tts import synthesize
from auth import get_current_user
import context

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
    from datetime import timezone as _tz
    parsed = dateparser.parse(
        due_hint,
        settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    if not parsed:
        return None
    # parsed is naive (local time) — convert to UTC so Supabase stores the right moment
    return parsed.astimezone(_tz.utc).isoformat()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/capture/stream")
async def capture_stream(
    session_id: str = Form(...),
    audio: UploadFile = File(None),
    text: str = Form(None),
    user: dict = Depends(get_current_user),
):
    if text and text.strip():
        transcript = text.strip()
    elif audio:
        audio_bytes = await audio.read()
        try:
            transcript = transcribe(audio_bytes, audio.content_type or "audio/webm")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"STT failed: {e}")
    else:
        raise HTTPException(status_code=422, detail="Either audio or text must be provided")

    if not transcript:
        raise HTTPException(status_code=422, detail="Empty transcript")

    context.current_user_id.set(user["sub"])

    similar = retrieve_similar(transcript)
    rag_context = _fmt_context(similar)

    async def event_stream():
        yield _sse({"type": "transcript", "text": transcript})

        spoken = ""
        metadata: dict = {"intent_type": "note", "should_store": False, "due_hint": None, "reminder_text": None}

        try:
            async for event in run_agentic_loop(session_id, transcript, rag_context):
                if event["type"] == "ack":
                    try:
                        ack_audio = await synthesize(event["text"])
                        yield _sse({"type": "ack_audio", "audio_base64": ack_audio, "text": event["text"]})
                    except Exception as e:
                        print(f"[TTS] ack synthesis failed: {e}")
                        yield _sse({"type": "error", "message": f"TTS (ack) failed: {e}"})
                elif event["type"] == "spoken":
                    spoken = event["text"]
                    yield _sse({"type": "spoken", "text": spoken})
                elif event["type"] == "metadata":
                    metadata = {k: v for k, v in event.items() if k != "type"}
                    yield _sse(event)
                else:
                    yield _sse(event)
        except Exception as exc:
            print(f"[agent_stream] agentic loop failed: {exc}", flush=True)
            yield _sse({"type": "error", "message": f"Agent error: {exc}"})
            yield _sse({"type": "done"})
            return

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
            except Exception as e:
                print(f"[TTS] final synthesis failed: {e}")
                yield _sse({"type": "error", "message": f"TTS failed: {e}"})

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

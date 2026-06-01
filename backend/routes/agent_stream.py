import asyncio
import json
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from stt import transcribe
from rag import retrieve_similar, store_item
from agent import run_agentic_loop
import base64
from tts import synthesize, synthesize_stream
from auth import get_current_user
from db import get_admin_db
from time_utils import parse_due_at, is_valid_iana
from supermemory_client import format_memory_context, get_user_profile, likely_memory_useful
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


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _should_store_capture(metadata: dict, spoken: str, handled_update_item: bool) -> bool:
    return bool(
        metadata.get("should_store")
        and not metadata.get("awaiting_clarification")
        and not handled_update_item
        and not metadata.get("update_only")
        and spoken
    )


@router.post("/capture/stream")
async def capture_stream(
    session_id: str = Form(...),
    audio: UploadFile = File(None),
    text: str = Form(None),
    timezone: str = Form("UTC"),
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

    safe_tz = timezone if (timezone and is_valid_iana(timezone)) else "UTC"

    # Scope user_id for retrieve_similar, then reset — generator handles its own scope
    _uid_tok = context.current_user_id.set(user["sub"])
    try:
        # Persist validated TZ to DB so cron/proactive jobs have real per-user TZ
        await asyncio.to_thread(
            lambda: get_admin_db().table("users")
                .update({"timezone": safe_tz})
                .eq("id", user["sub"])
                .execute()
        )

        similar = retrieve_similar(transcript)
        rag_context = _fmt_context(similar)
        user_memory_context = ""
        if likely_memory_useful(transcript):
            profile = await get_user_profile(user["sub"], transcript, timeout=1.0, allow_stale=True)
            user_memory_context = format_memory_context(profile)
    finally:
        context.current_user_id.reset(_uid_tok)

    async def event_stream():
        uid_tok = context.current_user_id.set(user["sub"])
        tz_tok = context.current_user_tz.set(safe_tz)
        try:
            yield _sse({"type": "transcript", "text": transcript})

            spoken = ""
            metadata: dict = {"intent_type": "note", "should_store": False, "due_hint": None, "reminder_text": None, "content": None, "awaiting_clarification": False, "update_only": False, "recurrence": None}
            handled_update_item = False

            try:
                async for event in run_agentic_loop(
                    session_id,
                    transcript,
                    rag_context,
                    user_tz=safe_tz,
                    user_memory_context=user_memory_context,
                    user_name=user.get("name", ""),
                ):
                    if event["type"] == "ack":
                        try:
                            async for chunk in synthesize_stream(event["text"]):
                                yield _sse({"type": "ack_audio_chunk", "data": base64.b64encode(chunk).decode()})
                            yield _sse({"type": "ack_audio_done", "text": event["text"]})
                        except Exception as e:
                            print(f"[TTS] ack synthesis failed: {e}")
                            yield _sse({"type": "error", "message": f"TTS (ack) failed: {e}"})
                    elif event["type"] == "token":
                        yield _sse(event)
                    elif event["type"] == "spoken":
                        spoken = event["text"]
                        yield _sse({"type": "spoken", "text": spoken})
                    elif event["type"] == "metadata":
                        metadata = {k: v for k, v in event.items() if k != "type"}
                        yield _sse(event)
                    elif event["type"] == "tool_result":
                        yield _sse(event)
                        data = event.get("data") or {}
                        if (
                            event.get("name") == "recall_update_item"
                            and data.get("updated") is True
                            and data.get("item_id")
                        ):
                            handled_update_item = True
                            yield _sse({
                                "type": "item_updated",
                                "item_id": data.get("item_id"),
                                "due_at": data.get("due_at"),
                            })
                    else:
                        yield _sse(event)
            except Exception as exc:
                print(f"[agent_stream] agentic loop failed: {exc}", flush=True)
                yield _sse({"type": "error", "message": f"Agent error: {exc}"})
                yield _sse({"type": "done"})
                return

            # Store item if requested (hard gate: never store on clarifying question turns)
            due_at = parse_due_at(metadata.get("due_hint"), safe_tz)
            item_id = None
            if _should_store_capture(metadata, spoken, handled_update_item):
                item_id = store_item(
                    content=metadata.get("content") or transcript,
                    intent_type=metadata.get("intent_type", "note"),
                    due_hint=metadata.get("due_hint"),
                    due_at=due_at,
                    reminder_text=metadata.get("reminder_text"),
                    recurrence=metadata.get("recurrence"),
                )

            yield _sse({
                "type": "stored",
                "item_id": item_id,
                "due_at": due_at,
            })

            # Synthesize TTS last — stream chunks; done is always emitted
            if spoken:
                try:
                    async for chunk in synthesize_stream(spoken):
                        yield _sse({"type": "audio_chunk", "data": base64.b64encode(chunk).decode()})
                    yield _sse({"type": "audio_done"})
                except Exception as e:
                    print(f"[TTS] final synthesis failed: {e}")
                    yield _sse({"type": "error", "message": f"TTS failed: {e}"})

            yield _sse({"type": "done"})

        except GeneratorExit:
            print("[agent_stream] client disconnected mid-stream")
        finally:
            context.current_user_id.reset(uid_tok)
            context.current_user_tz.reset(tz_tok)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

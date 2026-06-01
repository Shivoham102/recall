import asyncio
import json
import re
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from stt import transcribe
from rag import retrieve_similar, store_item
from agent import run_agentic_loop
import base64
from tts import synthesize_stream
from auth import get_current_user
from db import get_admin_db
from time_utils import parse_due_at, is_valid_iana
from supermemory_client import format_memory_context, get_user_profile, likely_memory_useful
import context

router = APIRouter()

_MD_EMPHASIS_RE = re.compile(r'[*`#_~]+')
_MD_BULLET_RE = re.compile(r'^\s*[-•*]\s+')
_WS_RE = re.compile(r'\s+')


def _clean_for_speech(s: str) -> str:
    """Strip markdown so the TTS engine doesn't read symbols aloud. The DISPLAYED
    text keeps its markdown; only the audio text is cleaned."""
    s = _MD_BULLET_RE.sub("", s.strip())   # leading bullet marker
    s = _MD_EMPHASIS_RE.sub("", s)         # ** * ` # _ ~ emphasis/headers
    s = _WS_RE.sub(" ", s).strip()         # collapse newlines/runs of whitespace
    return s


def _flush_chunks(buf: str) -> tuple[list[str], str]:
    """Extract complete speakable chunks from the streaming buffer, returning
    (chunks, leftover). Splits at a newline OR at .!?: followed by whitespace, so
    markdown briefings (bullets, headers, colons) flush early instead of waiting for
    a rare sentence-ending period + capital. The leftover stays RAW (markdown intact)
    so the next call's scan still sees real boundaries; each emitted chunk is cleaned
    for speech. Streaming-safe: a boundary is only cut once its trailing whitespace
    has arrived, so partial tokens are never cut mid-word."""
    chunks: list[str] = []
    last = 0
    i = 0
    n = len(buf)
    while i < n:
        ch = buf[i]
        end = -1
        if ch == "\n":
            end = i + 1
        elif ch in ".!?:" and i + 1 < n and buf[i + 1].isspace():
            end = i + 1
        if end != -1:
            cleaned = _clean_for_speech(buf[last:end])
            if cleaned:
                chunks.append(cleaned)
            last = end
            i = end
            continue
        i += 1
    return chunks, buf[last:]


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

        store_task = None
        tts_tasks: list[asyncio.Task] = []  # defined pre-try so finally can always cancel

        try:
            yield _sse({"type": "transcript", "text": transcript})

            spoken = ""
            metadata: dict = {"intent_type": "note", "should_store": False, "due_hint": None, "reminder_text": None, "content": None, "awaiting_clarification": False, "update_only": False, "recurrence": None}
            handled_update_item = False

            # Sentence-level TTS pipeline — runs concurrently with the LLM token
            # stream. Each (re)start gets its own queues + worker so that audio
            # synthesized from intermediate text (e.g. an ack before a tool call) can
            # be discarded wholesale by abandoning that worker's queues.
            sentence_buf = ""

            def _start_pipeline():
                sq: asyncio.Queue[str | None] = asyncio.Queue()
                aq: asyncio.Queue[bytes | None] = asyncio.Queue()

                async def worker():
                    # Pull completed sentences in order, synthesize each (mp3) and
                    # stream its chunks. Sentence 1 is synthesized while the model is
                    # still writing later sentences.
                    try:
                        while True:
                            sentence = await sq.get()
                            if sentence is None:
                                break
                            async for chunk in synthesize_stream(sentence):
                                await aq.put(chunk)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        print(f"[TTS] sentence streaming failed: {e}")
                    finally:
                        # Non-blocking sentinel (queue is unbounded) so this is safe
                        # even when the worker is being cancelled.
                        aq.put_nowait(None)

                task = asyncio.create_task(worker())
                tts_tasks.append(task)
                return sq, aq, task

            llm_sentence_q, audio_chunk_q, tts_task = _start_pipeline()

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
                        sentence_buf += event["text"]
                        yield _sse(event)  # raw token (with markdown) to the UI
                        chunks, sentence_buf = _flush_chunks(sentence_buf)
                        for c in chunks:
                            await llm_sentence_q.put(c)
                    elif event["type"] == "tool_call":
                        # Text streamed before a tool call was an ack/intermediate, not
                        # the final response. Discard its pipeline AND any audio already
                        # synthesized from it, then start a fresh pipeline for the
                        # (possibly final) response that follows the tool results.
                        tts_task.cancel()
                        llm_sentence_q, audio_chunk_q, tts_task = _start_pipeline()
                        sentence_buf = ""
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

                    # Forward any audio synthesized SO FAR — don't wait for the whole
                    # agent loop to finish. This is the key to low latency: the first
                    # chunk ("Here's your briefing:") is ready early and gets streamed
                    # to the client while later text is still being generated, instead
                    # of sitting in the queue until the loop ends.
                    while True:
                        try:
                            chunk = audio_chunk_q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if chunk is None:
                            continue  # sentinel from a cancelled worker — ignore mid-loop
                        yield _sse({"type": "audio_chunk", "data": base64.b64encode(chunk).decode()})
            except Exception as exc:
                print(f"[agent_stream] agentic loop failed: {exc}", flush=True)
                yield _sse({"type": "error", "message": f"Agent error: {exc}"})
                yield _sse({"type": "done"})
                return

            # Flush remaining buffer tail (cleaned for speech) and signal tts_worker
            # to finish.
            tail = _clean_for_speech(sentence_buf)
            if tail:
                await llm_sentence_q.put(tail)
            await llm_sentence_q.put(None)  # sentinel → tts_worker exits

            # Kick off DB store concurrently with audio drain
            due_at = parse_due_at(metadata.get("due_hint"), safe_tz)
            if _should_store_capture(metadata, spoken, handled_update_item):
                store_task = asyncio.create_task(
                    asyncio.to_thread(
                        store_item,
                        content=metadata.get("content") or transcript,
                        intent_type=metadata.get("intent_type", "note"),
                        due_hint=metadata.get("due_hint"),
                        due_at=due_at,
                        reminder_text=metadata.get("reminder_text"),
                        recurrence=metadata.get("recurrence"),
                    )
                )

            # Drain any REMAINING audio chunks — those synthesized after the loop
            # ended (later sentences). Early chunks were already streamed mid-loop.
            if spoken:
                try:
                    while True:
                        chunk = await audio_chunk_q.get()
                        if chunk is None:
                            break
                        yield _sse({"type": "audio_chunk", "data": base64.b64encode(chunk).decode()})
                    yield _sse({"type": "audio_done"})
                except Exception as e:
                    print(f"[TTS] drain failed: {e}")
                    yield _sse({"type": "error", "message": f"TTS failed: {e}"})

            item_id = (await store_task) if store_task else None
            yield _sse({
                "type": "stored",
                "item_id": item_id,
                "due_at": due_at,
            })

            yield _sse({"type": "done"})

        except GeneratorExit:
            print("[agent_stream] client disconnected mid-stream")
        finally:
            context.current_user_id.reset(uid_tok)
            context.current_user_tz.reset(tz_tok)
            for t in tts_tasks:
                if not t.done():
                    t.cancel()
            if store_task and not store_task.done():
                store_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

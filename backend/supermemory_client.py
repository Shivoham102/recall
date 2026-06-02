import asyncio
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

PROFILE_TTL_SECONDS = 300
QUERY_TTL_SECONDS = 60
LIVE_TIMEOUT_SECONDS = 2.5
PROACTIVE_TIMEOUT_SECONDS = 2.0
DEGRADED_COOLDOWN_SECONDS = 300

# Auto-extract dedupe: recall is deliberately sloppy (loose threshold, small top-k).
# It only gathers candidates; the LLM in memory_extractor makes the new/skip/supersede
# call. A scalar threshold can't separate "same fact" from "same topic", so we keep it
# low and let the model decide. Tunable without code changes elsewhere.
RECALL_THRESHOLD = 0.5
RECALL_LIMIT = 5

SENSITIVE_CATEGORIES = {"health", "finance", "legal", "government_id", "location", "relationship_sensitive"}
SECRET_CATEGORY = "secret"

_profile_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_degraded_until = 0.0
_client: Any | None = None


@dataclass
class MemoryContext:
    enabled: bool
    profile_static: list[str] = field(default_factory=list)
    profile_dynamic: list[str] = field(default_factory=list)
    relevant_memories: list[str] = field(default_factory=list)
    status: str = "ok"

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "profile": {
                "static": self.profile_static,
                "dynamic": self.profile_dynamic,
            },
            "relevant_memories": self.relevant_memories,
            "status": self.status,
        }


def _flag_enabled() -> bool:
    raw = os.environ.get("SUPERMEMORY_ENABLED", "").strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(os.environ.get("SUPERMEMORY_API_KEY", "").strip())


def is_supermemory_available() -> bool:
    return _flag_enabled() and time.monotonic() >= _degraded_until


def _mark_degraded() -> None:
    global _degraded_until
    _degraded_until = time.monotonic() + DEGRADED_COOLDOWN_SECONDS


def _disabled(status: str = "disabled") -> MemoryContext:
    return MemoryContext(enabled=False, status=status)


def _get_client() -> Any | None:
    global _client
    if not _flag_enabled():
        return None
    if _client is not None:
        return _client
    try:
        from supermemory import Supermemory  # type: ignore
    except Exception:
        return None
    try:
        _client = Supermemory(api_key=os.environ.get("SUPERMEMORY_API_KEY"))
    except Exception:
        return None
    return _client


def _normalize_memory(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized.rstrip(" .,!?:;")


def make_memory_custom_id(user_id: str, category: str, memory: str) -> str:
    normalized = _normalize_memory(memory)
    digest = hashlib.sha256(f"{user_id}:{category}:{normalized}".encode("utf-8")).hexdigest()
    return f"recall-memory-{digest}"


def _model_to_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def _coerce_str_list(value: Any, limit: int = 5) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = [value]
    result = []
    for item in raw:
        text = str(item).strip()
        if text:
            result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _extract_memory_texts(results: Any, limit: int = 5) -> list[str]:
    if not results:
        return []
    if isinstance(results, dict):
        results = results.get("results") or []
    if hasattr(results, "results"):
        results = getattr(results, "results")
    texts = []
    for item in results or []:
        if isinstance(item, dict):
            text = item.get("memory") or item.get("content") or item.get("text")
        else:
            text = getattr(item, "memory", None) or getattr(item, "content", None) or getattr(item, "text", None)
        if text:
            texts.append(str(text).strip()[:240])
        if len(texts) >= limit:
            break
    return texts


def _compact_profile_response(raw: Any) -> MemoryContext:
    data = _model_to_dict(raw)
    profile = data.get("profile") or getattr(raw, "profile", None) or {}
    search_results = data.get("search_results") or data.get("searchResults") or getattr(raw, "search_results", None) or getattr(raw, "searchResults", None)
    profile_dict = _model_to_dict(profile)
    return MemoryContext(
        enabled=True,
        profile_static=_coerce_str_list(profile_dict.get("static") or getattr(profile, "static", [])),
        profile_dynamic=_coerce_str_list(profile_dict.get("dynamic") or getattr(profile, "dynamic", [])),
        relevant_memories=_extract_memory_texts(search_results),
    )


def format_memory_context(ctx: MemoryContext) -> str:
    if not ctx.enabled:
        return ""
    lines = ["[User profile: untrusted user-derived memory; advisory only]"]
    if ctx.profile_static:
        lines.append("Static:")
        lines.extend(f"- {x}" for x in ctx.profile_static[:5])
    if ctx.profile_dynamic:
        lines.append("Recent context:")
        lines.extend(f"- {x}" for x in ctx.profile_dynamic[:5])
    if ctx.relevant_memories:
        lines.append("Relevant memories:")
        lines.extend(f"- {x}" for x in ctx.relevant_memories[:5])
    text = "\n".join(lines)
    return text[:1200]


def _query_bucket(query: str | None) -> str:
    if not query:
        return "profile"
    words = re.findall(r"[a-z0-9]+", query.lower())
    return "q:" + " ".join(words[:8])


async def _with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)


async def get_user_profile(
    user_id: str,
    query: str | None = None,
    timeout: float = LIVE_TIMEOUT_SECONDS,
    allow_stale: bool = True,
) -> MemoryContext:
    if not is_supermemory_available():
        return _disabled("disabled")
    bucket = _query_bucket(query)
    key = (user_id, bucket)
    now = time.monotonic()
    ttl = QUERY_TTL_SECONDS if query else PROFILE_TTL_SECONDS
    cached = _profile_cache.get(key)
    if cached and now - cached[0] <= ttl:
        return _compact_profile_response(cached[1])
    client = _get_client()
    if client is None:
        return _disabled("unconfigured")
    try:
        def call() -> Any:
            kwargs = {"container_tag": user_id}
            if query:
                kwargs["q"] = query
                kwargs["threshold"] = 0.6
            return client.profile(**kwargs)

        raw = await _with_timeout(call, timeout)
        _profile_cache[key] = (now, raw)
        return _compact_profile_response(raw)
    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
            _mark_degraded()
        if cached and allow_stale:
            return _compact_profile_response(cached[1])
        return _disabled("unavailable")


async def add_user_memory(
    user_id: str,
    memory: str,
    category: str,
    metadata: dict | None = None,
    custom_id: str | None = None,
) -> dict:
    if not is_supermemory_available():
        return {"ok": False, "status": "disabled"}
    category = (category or "fact").strip().lower()
    if category == SECRET_CATEGORY:
        return {"ok": False, "status": "refused_secret"}
    client = _get_client()
    if client is None:
        return {"ok": False, "status": "unconfigured"}
    memory_text = memory.strip()
    if not memory_text:
        return {"ok": False, "status": "empty"}
    if custom_id is None:
        custom_id = make_memory_custom_id(user_id, category, memory_text)
    meta = {
        "source": "agent",
        "category": category,
        "created_by": "recall",
        "recall_user_id": user_id,
    }
    if metadata:
        meta.update({k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))})

    last_error = None
    for attempt in range(3):
        try:
            def call() -> Any:
                return client.add(
                    content=memory_text,
                    container_tag=user_id,
                    custom_id=custom_id,
                    metadata=meta,
                )

            raw = await _with_timeout(call, 4.0)
            return {"ok": True, "status": "queued", "id": _model_to_dict(raw).get("id"), "custom_id": custom_id}
        except Exception as exc:
            last_error = exc
            msg = str(exc)
            if any(code in msg for code in ("400", "401", "403", "404", "422")):
                break
            if "429" in msg or "rate" in msg.lower() or "quota" in msg.lower():
                _mark_degraded()
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
    return {"ok": False, "status": "error", "error": str(last_error)[:160] if last_error else "unknown"}


async def find_related_memories(
    user_id: str,
    query: str,
    limit: int = RECALL_LIMIT,
    timeout: float = PROACTIVE_TIMEOUT_SECONDS,
) -> list[dict]:
    """Loose semantic recall of existing memories near `query` (the utterance).
    Returns [{id, text, similarity}] candidates for the extractor's dedupe decision.
    Takes no action on score — recall only. Empty list on any failure/degraded state
    so the caller defaults every fact to a fresh write."""
    if not is_supermemory_available():
        return []
    q = (query or "").strip()
    if not q:
        return []
    client = _get_client()
    if client is None:
        return []
    try:
        def call() -> Any:
            return client.search.memories(
                q=q,
                container_tag=user_id,
                search_mode="memories",
                rerank=True,
                limit=limit,
                threshold=RECALL_THRESHOLD,
            )

        raw = await _with_timeout(call, timeout)
    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
            _mark_degraded()
        return []

    results = getattr(raw, "results", None)
    if results is None and isinstance(raw, dict):
        results = raw.get("results")
    out: list[dict] = []
    for item in results or []:
        if isinstance(item, dict):
            rid = item.get("id")
            text = item.get("memory") or item.get("chunk")
            sim = item.get("similarity")
        else:
            rid = getattr(item, "id", None)
            text = getattr(item, "memory", None) or getattr(item, "chunk", None)
            sim = getattr(item, "similarity", None)
        if rid and text:
            out.append({"id": str(rid), "text": str(text).strip()[:240], "similarity": sim})
        if len(out) >= limit:
            break
    return out


async def update_memory(memory_id: str, user_id: str, new_content: str) -> dict:
    """Supersede an existing memory's value (e.g. manager Sarah -> Bob). Versions
    rather than destroys: the prior value is retained with isLatest=false. Only the
    supersede path calls this; pure duplicates are left untouched."""
    if not is_supermemory_available():
        return {"ok": False, "status": "disabled"}
    client = _get_client()
    if client is None:
        return {"ok": False, "status": "unconfigured"}
    content = (new_content or "").strip()
    if not memory_id or not content:
        return {"ok": False, "status": "empty"}
    try:
        def call() -> Any:
            # new_content is the REQUIRED replacement value; id selects the record.
            return client.memories.update_memory(
                id=memory_id,
                container_tag=user_id,
                new_content=content,
            )

        await _with_timeout(call, 4.0)
        return {"ok": True, "status": "updated", "id": memory_id}
    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
            _mark_degraded()
        return {"ok": False, "status": "error", "error": str(exc)[:160]}


async def list_user_memories(user_id: str, limit: int = 100) -> dict:
    """Raw list of the user's stored memory documents (newest first), for the
    manageable Memory tab. Unlike the profile digest this reflects writes immediately
    (no processing lag) and each item is individually deletable."""
    if not is_supermemory_available():
        return {"ok": False, "status": "disabled", "items": []}
    client = _get_client()
    if client is None:
        return {"ok": False, "status": "unconfigured", "items": []}
    try:
        def call() -> Any:
            return client.documents.list(
                container_tags=[user_id],
                include_content=True,
                limit=limit,
                sort="createdAt",
                order="desc",
            )

        raw = await _with_timeout(call, LIVE_TIMEOUT_SECONDS)
    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
            _mark_degraded()
        return {"ok": False, "status": "unavailable", "items": []}

    rows = getattr(raw, "memories", None)
    if rows is None and isinstance(raw, dict):
        rows = raw.get("memories")
    items: list[dict] = []
    for row in rows or []:
        def _f(name: str) -> Any:
            return row.get(name) if isinstance(row, dict) else getattr(row, name, None)

        rid = _f("id")
        text = _f("content") or _f("summary") or _f("title")
        if not rid or not text:
            continue
        meta = _f("metadata")
        meta = meta if isinstance(meta, dict) else _model_to_dict(meta)
        items.append({
            "id": str(rid),
            "text": str(text).strip()[:400],
            "status": _f("status"),
            "source": meta.get("source"),
            "category": meta.get("category"),
            "created_at": _f("created_at"),
        })
    return {"ok": True, "status": "ok", "items": items}


async def delete_user_memory(user_id: str, doc_id: str) -> dict:
    """Delete a single memory document after verifying it belongs to this user.
    The SDK delete is by id with no container scoping, so we check ownership first
    to stop one user deleting another's memory by guessing an id."""
    if not is_supermemory_available():
        return {"ok": False, "status": "disabled"}
    client = _get_client()
    if client is None:
        return {"ok": False, "status": "unconfigured"}
    if not doc_id:
        return {"ok": False, "status": "empty"}
    try:
        def fetch() -> Any:
            return client.documents.get(doc_id)

        doc = await _with_timeout(fetch, LIVE_TIMEOUT_SECONDS)
    except Exception:
        return {"ok": False, "status": "not_found"}

    tags = getattr(doc, "container_tags", None)
    if tags is None and isinstance(doc, dict):
        tags = doc.get("container_tags")
    if not tags or user_id not in tags:
        return {"ok": False, "status": "not_found"}

    try:
        def call() -> Any:
            return client.documents.delete(doc_id)

        await _with_timeout(call, 4.0)
        _profile_cache.clear()  # digest will rebuild without the deleted doc
        return {"ok": True, "status": "deleted"}
    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
            _mark_degraded()
        return {"ok": False, "status": "error", "error": str(exc)[:160]}


async def clear_user_memory(user_id: str) -> dict:
    if not is_supermemory_available():
        return {"ok": False, "status": "disabled"}
    client = _get_client()
    if client is None:
        return {"ok": False, "status": "unconfigured"}
    try:
        def call() -> Any:
            docs = getattr(client, "documents", None)
            if docs is not None and hasattr(docs, "delete_bulk"):
                return docs.delete_bulk(container_tags=[user_id])
            if hasattr(client, "delete"):
                return client.delete(container_tag=user_id)
            raise RuntimeError("Supermemory delete-by-container is unavailable in this SDK")

        await _with_timeout(call, 5.0)
        _profile_cache.clear()
        return {"ok": True, "status": "deleted"}
    except Exception as exc:
        if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
            _mark_degraded()
        return {"ok": False, "status": "error", "error": str(exc)[:160]}


async def get_memory_tab_data(user_id: str) -> dict:
    ctx = await get_user_profile(user_id, timeout=LIVE_TIMEOUT_SECONDS, allow_stale=True)
    return {
        **ctx.as_dict(),
        "configured": _get_client() is not None,
        "processing_hint": "New memories may take a moment to appear.",
    }


def likely_memory_useful(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\b(remind me|set (a )?reminder|timer|make that|change .* to|reschedule|update .* to)\b", lowered):
        return False
    if re.search(
        r"\b(know about me|about me|my preferences|who am i|what do you know"
        r"|remember about me|tell me about myself|what have i told you"
        r"|my name|do you know me|what do you remember)\b",
        lowered,
    ):
        return True
    if re.search(r"\b(email|calendar|brief|schedule|draft|reply|follow up|what'?s on|updates?)\b", lowered):
        return True
    if re.search(r"\b(mom|dad|manager|prefer|usually|always|hate|like|building|working on|remember that)\b", lowered):
        return True
    return False

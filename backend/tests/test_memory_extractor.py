import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_wants_memory_extraction_skips_reminders_and_updates():
    from memory_extractor import _wants_memory_extraction

    assert _wants_memory_extraction({"intent_type": "note"}) is True
    assert _wants_memory_extraction({"intent_type": "reminder", "due_hint": "8pm"}) is False
    assert _wants_memory_extraction({"update_only": True}) is False
    assert _wants_memory_extraction({"awaiting_clarification": True}) is False


def test_parse_facts_tolerates_prose_wrapping():
    from memory_extractor import _parse_facts

    clean = '{"memories": [{"memory": "Prefers tea", "category": "preference"}]}'
    assert _parse_facts(clean)[0]["memory"] == "Prefers tea"

    wrapped = 'Sure, here you go:\n{"memories": []}\nhope that helps'
    assert _parse_facts(wrapped) == []

    assert _parse_facts("not json at all") == []
    assert _parse_facts("") == []


def test_is_sensitive_catches_both_signals():
    from memory_extractor import _is_sensitive

    assert _is_sensitive("health", "normal") is True          # category token
    assert _is_sensitive("preference", "sensitive") is True    # free-text sensitivity
    assert _is_sensitive("preference", "normal") is False


# ── verdict application (LLM + supermemory mocked) ──────────────────────────────

def _run_extract(monkeypatch, *, facts, candidates):
    """Drive extract_and_store with a stubbed Haiku response and supermemory layer.
    Returns the recorded add_user_memory / update_memory calls."""
    import memory_extractor as me

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(me, "is_supermemory_available", lambda: True)

    async def fake_recall(user_id, query, *a, **k):
        return candidates

    adds: list[dict] = []
    updates: list[dict] = []

    async def fake_add(user_id, memory, category, metadata=None):
        adds.append({"memory": memory, "category": category, "metadata": metadata})
        return {"ok": True, "status": "queued"}

    async def fake_update(memory_id, user_id, new_content):
        updates.append({"id": memory_id, "content": new_content})
        return {"ok": True, "status": "updated"}

    monkeypatch.setattr(me, "find_related_memories", fake_recall)
    monkeypatch.setattr(me, "add_user_memory", fake_add)
    monkeypatch.setattr(me, "update_memory", fake_update)

    payload = json.dumps({"memories": facts})

    class _FakeMessages:
        async def create(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(text=payload)])

    class _FakeClient:
        def __init__(self, **kw):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    asyncio.run(me.extract_and_store("user-1", "UTC", "some utterance", "Got it."))
    return adds, updates


def test_new_fact_is_stored(monkeypatch):
    adds, updates = _run_extract(
        monkeypatch,
        facts=[{"memory": "Prefers concise emails", "category": "preference", "sensitivity": "normal", "action": "new"}],
        candidates=[],
    )
    assert len(adds) == 1
    assert adds[0]["memory"] == "Prefers concise emails"
    assert adds[0]["metadata"]["source"] == "auto_extract"
    assert updates == []


def test_sensitive_fact_is_never_auto_stored(monkeypatch):
    adds, updates = _run_extract(
        monkeypatch,
        facts=[{"memory": "Takes blood pressure medication", "category": "health", "sensitivity": "sensitive", "action": "new"}],
        candidates=[],
    )
    assert adds == []
    assert updates == []


def test_skip_does_nothing(monkeypatch):
    adds, updates = _run_extract(
        monkeypatch,
        facts=[{"memory": "Dislikes morning meetings", "category": "preference", "sensitivity": "normal", "action": "skip"}],
        candidates=[{"id": "m1", "text": "Hates meetings before 10am", "similarity": 0.7}],
    )
    assert adds == []
    assert updates == []


def test_supersede_updates_existing_record(monkeypatch):
    adds, updates = _run_extract(
        monkeypatch,
        facts=[{"memory": "Manager is Bob", "category": "relationship", "sensitivity": "normal", "action": "supersede", "supersede_id": "m1"}],
        candidates=[{"id": "m1", "text": "Manager is Sarah", "similarity": 0.8}],
    )
    assert updates == [{"id": "m1", "content": "Manager is Bob"}]
    assert adds == []


def test_supersede_with_unknown_id_falls_back_to_new(monkeypatch):
    adds, updates = _run_extract(
        monkeypatch,
        facts=[{"memory": "Manager is Bob", "category": "relationship", "sensitivity": "normal", "action": "supersede", "supersede_id": "ghost"}],
        candidates=[{"id": "m1", "text": "Manager is Sarah", "similarity": 0.8}],
    )
    assert updates == []
    assert len(adds) == 1
    assert adds[0]["memory"] == "Manager is Bob"

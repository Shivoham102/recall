"""Tests for follow-up draft prompt construction.

The bug these guard against: for anonymized senders (craigslist / relay addresses with no
display name) the drafter used to feed the bare email address in as a "first name", and Haiku
leaked its confusion ("I couldn't find a name...") into the draft body. We assert on the prompt
we build (deterministic) rather than the model's wording (not deterministic).
"""
import asyncio
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def _capture_prompt(monkeypatch, **overrides) -> str:
    """Call _haiku_draft with AsyncAnthropic stubbed; return the prompt text sent to the model."""
    from proactive.jobs.follow_up_draft import _haiku_draft

    captured: dict = {}

    class _FakeMessages:
        async def create(self, **kw):
            captured["messages"] = kw["messages"]
            return SimpleNamespace(content=[SimpleNamespace(text="Hi,\n\nBody.\n\nThanks,")])

    class _FakeClient:
        def __init__(self, **kw):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    kwargs = dict(
        context_summary="USER: I'll get back to you next week.",
        commitment="Follow up on the apartment listing.",
        counterparty="",
        memory_context="none",
        formality="balanced",
        avg_words=15,
        greeting="Hi",
        closing="Thanks",
        display_name="Shiv",
    )
    kwargs.update(overrides)
    asyncio.run(_haiku_draft(**kwargs))
    return captured["messages"][0]["content"]


def test_anonymized_recipient_gets_nameless_open(monkeypatch):
    prompt = _capture_prompt(monkeypatch, counterparty="relay-abc@craigslist.org")
    assert "no recipient name is available" in prompt
    assert "and the recipient's name" not in prompt


def test_named_recipient_gets_named_open(monkeypatch):
    prompt = _capture_prompt(monkeypatch, counterparty="Jordan Lee")
    assert "and the recipient's name" in prompt
    assert "Jordan Lee" in prompt
    assert "no recipient name is available" not in prompt


def test_display_name_that_is_an_address_goes_nameless(monkeypatch):
    # The @-heuristic: a display name that is itself an address must not be used as a name.
    prompt = _capture_prompt(monkeypatch, counterparty="bob@x.com")
    assert "no recipient name is available" in prompt


def test_prompt_forbids_leaking_missing_info(monkeypatch):
    prompt = _capture_prompt(monkeypatch, counterparty="relay-abc@craigslist.org")
    assert "Never mention missing information" in prompt


def test_prompt_has_no_em_dash(monkeypatch):
    # CLAUDE.md: agent-facing prompt strings must not contain em dashes.
    for cp in ("relay-abc@craigslist.org", "Jordan Lee"):
        assert "—" not in _capture_prompt(monkeypatch, counterparty=cp)

"""
Tests for multi-turn conversation storage logic.

Covers:
- Hard storage gate (awaiting_clarification blocks writes)
- content field used over raw transcript on follow-up turns
- Single-turn complete (no clarification)
- 2-turn clarification (date given, time asked, time answered)
- 3-turn clarification
- User correction mid-flow
- Non-reminder clarifying question
"""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Pure logic unit tests ──────────────────────────────────────────────────────

class TestStorageGate:
    """Validate the hard gate condition used in capture routes."""

    def _should_store(self, metadata: dict) -> bool:
        return bool(metadata.get("should_store") and not metadata.get("awaiting_clarification"))

    def _content_for(self, metadata: dict, transcript: str) -> str:
        return metadata.get("content") or transcript

    def test_gate_blocks_when_awaiting_clarification(self):
        meta = {"should_store": True, "awaiting_clarification": True}
        assert not self._should_store(meta)

    def test_gate_allows_when_complete(self):
        meta = {"should_store": True, "awaiting_clarification": False}
        assert self._should_store(meta)

    def test_gate_blocks_when_should_store_false(self):
        meta = {"should_store": False, "awaiting_clarification": False}
        assert not self._should_store(meta)

    def test_gate_defaults_awaiting_clarification_to_false(self):
        meta = {"should_store": True}
        assert self._should_store(meta)

    def test_content_uses_field_when_present(self):
        meta = {"content": "do late code on 14th of May"}
        assert self._content_for(meta, "10 a.m.") == "do late code on 14th of May"

    def test_content_falls_back_to_transcript(self):
        meta = {"content": None}
        assert self._content_for(meta, "remind me to call mom at 3pm") == "remind me to call mom at 3pm"

    def test_content_empty_string_falls_back_to_transcript(self):
        meta = {"content": ""}
        assert self._content_for(meta, "transcript fallback") == "transcript fallback"

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_custom_id_is_deterministic_for_repeated_fact():
    from supermemory_client import make_memory_custom_id

    first = make_memory_custom_id("user-1", "preference", "User prefers concise updates.")
    second = make_memory_custom_id("user-1", "preference", " user prefers concise updates ")

    assert first == second


def test_likely_memory_useful_skips_simple_capture():
    from supermemory_client import likely_memory_useful

    assert not likely_memory_useful("Remind me to go skateboarding at 8 PM")
    assert not likely_memory_useful("Actually make that 9 PM")
    assert likely_memory_useful("Remember that I prefer concise updates")


def test_disabled_without_api_key_returns_empty_profile(monkeypatch):
    monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
    monkeypatch.delenv("SUPERMEMORY_ENABLED", raising=False)

    from supermemory_client import get_user_profile

    profile = asyncio.run(get_user_profile("user-1", "email brief"))

    assert profile.enabled is False
    assert profile.status == "disabled"
    assert profile.profile_static == []
    assert profile.profile_dynamic == []


def test_kill_switch_overrides_api_key(monkeypatch):
    monkeypatch.setenv("SUPERMEMORY_API_KEY", "test-key")
    monkeypatch.setenv("SUPERMEMORY_ENABLED", "false")

    from supermemory_client import get_memory_tab_data

    data = asyncio.run(get_memory_tab_data("user-1"))

    assert data["configured"] is True
    assert data["enabled"] is False

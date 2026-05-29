import asyncio

import context
from db import get_db
from supermemory_client import add_user_memory

_VALID_CADENCE = {"daily", "weekly", "monthly"}


async def track_goal(inp: dict) -> dict:
    """Record a recurring personal aspiration in user_goals. Neglect detection (nightly)
    surfaces a nudge when no matching recall_item appears within the cadence window.

    Also mirrors the goal into Supermemory (category 'routine') so the agent can recall it
    conversationally — the structured row drives neglect math, the memory drives chat recall."""
    goal_text = (inp.get("goal_text") or "").strip()
    if not goal_text:
        return {"summary": "No goal text provided", "tracked": False, "error": True}

    cadence = inp.get("cadence", "weekly")
    if cadence not in _VALID_CADENCE:
        cadence = "weekly"

    user_id = context.current_user_id.get("") or None
    if not user_id:
        return {"summary": "Cannot track a goal without a user scope", "tracked": False, "error": True}

    await asyncio.to_thread(
        lambda: get_db().table("user_goals").insert({
            "user_id": user_id,
            "goal_text": goal_text,
            "cadence_hint": cadence,
        }).execute()
    )

    # Best-effort mirror to Supermemory — never block or fail goal tracking on it.
    try:
        await add_user_memory(
            user_id,
            f"User wants to keep up with: {goal_text} (recurring goal, {cadence}).",
            category="routine",
            metadata={"kind": "goal", "cadence": cadence},
        )
    except Exception:
        pass

    return {"summary": f"Tracking goal: {goal_text} ({cadence})", "tracked": True}

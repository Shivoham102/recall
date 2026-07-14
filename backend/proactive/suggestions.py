"""
Shared helpers for agent_suggestions (the actionable proactive-suggestion backbone).

Both detectors (recurring-reminder detection and goal-neglect detection in pattern_learn)
write through upsert_suggestion, which implements status-aware re-arm:
  - absent              → insert pending
  - dismissed > 60 days → reset to pending (habit persisted; nudge again)
  - dismissed < 60 days → leave dismissed (cooldown)
  - accepted            → never re-suggest for this SAME dedupe_key
  - pending             → refresh title/payload

"Never re-suggest" is a true forever-skip only for callers that reuse a permanent
dedupe_key (recurring_reminder: accepting it converts the habit into an actual
recurring reminder, so it must never be re-asked). goal_neglect's neglected_goal
suggestions use a date-scoped dedupe_key instead, so an old accepted row simply
stops mattering once a new detection cycle writes a fresh key — the goal can be
nudged again next cycle without touching this function's contract.

All queries are admin-scoped; callers must pass an explicit user_id.
"""
from datetime import datetime, timedelta, timezone

_REARM_DAYS = 60


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def upsert_suggestion(
    db,
    user_id: str,
    kind: str,
    dedupe_key: str,
    title: str,
    payload: dict,
) -> str:
    """Insert or re-arm a pending suggestion. Returns the action taken
    ('inserted' | 'rearmed' | 'refreshed' | 'skipped')."""
    now = datetime.now(timezone.utc)
    existing_res = (
        db.table("agent_suggestions")
        .select("id, status, acted_at")
        .eq("user_id", user_id)
        .eq("kind", kind)
        .eq("dedupe_key", dedupe_key)
        .limit(1)
        .execute()
    )
    existing = (existing_res.data or [None])[0]

    if not existing:
        db.table("agent_suggestions").insert({
            "user_id": user_id,
            "kind": kind,
            "status": "pending",
            "title": title,
            "payload": payload,
            "dedupe_key": dedupe_key,
            "created_at": now.isoformat(),
        }).execute()
        return "inserted"

    status = existing.get("status")
    if status == "accepted":
        return "skipped"

    if status == "dismissed":
        acted = _parse_ts(existing.get("acted_at"))
        if acted is None or (now - acted) < timedelta(days=_REARM_DAYS):
            return "skipped"  # still in cooldown
        db.table("agent_suggestions").update({
            "status": "pending",
            "title": title,
            "payload": payload,
            "created_at": now.isoformat(),
            "acted_at": None,
        }).eq("id", existing["id"]).execute()
        return "rearmed"

    # pending → refresh content only
    db.table("agent_suggestions").update({
        "title": title,
        "payload": payload,
    }).eq("id", existing["id"]).execute()
    return "refreshed"

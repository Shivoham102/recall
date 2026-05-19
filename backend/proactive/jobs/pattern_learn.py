"""
Pattern learn — scripted analysis (runs nightly at 2am).

Counts recall_items by intent_type over the last 30 days, upserts into
user_behavior_patterns, and promotes patterns to auto_run when
frequency >= 3 AND confidence >= 0.8 (confidence = days_active / 7).

Delivers only when a pattern is newly promoted; silent otherwise.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from db import get_admin_db
from proactive.runner import ProactiveResult

_LOOKBACK_DAYS = 30
_SHOW_THRESHOLD = 2      # visible in Profile tab
_AUTO_RUN_FREQ = 3       # minimum frequency for auto_run consideration
_AUTO_RUN_CONFIDENCE = 0.8  # minimum confidence for auto_run promotion

_INTENT_LABELS: dict[str, str] = {
    "task": "task capture",
    "reminder": "reminder setting",
    "query": "information lookup",
    "note": "note taking",
    "blocker": "blocker tracking",
    "follow_up": "follow-up tracking",
    "progress": "progress updates",
    "update": "status updates",
}


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    db = get_admin_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=_LOOKBACK_DAYS)).isoformat()

    # Count recall_items by intent_type in the last 30 days
    res = (
        db.table("recall_items")
        .select("intent_type, created_at")
        .eq("user_id", user_id)
        .gte("created_at", cutoff)
        .execute()
    )
    items = res.data or []

    if not items:
        return ProactiveResult(
            text="Pattern learning complete — not enough data yet",
            job_type="pattern_learn",
            deliver=False,
        )

    counts = Counter(
        item["intent_type"]
        for item in items
        if item.get("intent_type")
    )

    # Load existing patterns for this user
    existing_res = (
        db.table("user_behavior_patterns")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )
    existing: dict[str, dict] = {
        row["query_template"]: row
        for row in (existing_res.data or [])
        if row.get("pattern_type") == "intent_frequency"
    }

    newly_promoted: list[str] = []

    for intent_type, count in counts.items():
        if count < _SHOW_THRESHOLD:
            continue

        label = _INTENT_LABELS.get(intent_type, intent_type.replace("_", " "))
        existing_row = existing.get(label)

        if existing_row:
            first_seen_raw = existing_row.get("first_seen_at", now.isoformat())
            try:
                _parsed = datetime.fromisoformat(first_seen_raw.replace("Z", "+00:00"))
                first_seen = _parsed.replace(tzinfo=timezone.utc) if _parsed.tzinfo is None else _parsed.astimezone(timezone.utc)
            except Exception:
                first_seen = now

            days_active = max(1, (now - first_seen).days)
            confidence = round(min(1.0, days_active / 7.0), 3)
            was_auto_run = existing_row.get("auto_run", False)
            new_auto_run = count >= _AUTO_RUN_FREQ and confidence >= _AUTO_RUN_CONFIDENCE

            db.table("user_behavior_patterns").update({
                "frequency": count,
                "confidence": confidence,
                "auto_run": new_auto_run,
                "last_seen_at": now.isoformat(),
            }).eq("id", existing_row["id"]).execute()

            if new_auto_run and not was_auto_run:
                newly_promoted.append(label)
        else:
            # Find earliest item creation for this intent_type as first_seen
            first_seen_iso = min(
                (item["created_at"] for item in items if item.get("intent_type") == intent_type),
                default=now.isoformat(),
            )
            db.table("user_behavior_patterns").insert({
                "user_id": user_id,
                "pattern_type": "intent_frequency",
                "query_template": label,
                "frequency": count,
                "auto_run": False,
                "confidence": 0.0,
                "first_seen_at": first_seen_iso,
                "last_seen_at": now.isoformat(),
            }).execute()

    if newly_promoted:
        labels_str = ", ".join(newly_promoted)
        text = (
            f"I've learned your patterns — starting to include {labels_str} "
            "in your morning brief automatically."
        )
        return ProactiveResult(text=text, job_type="pattern_learn", deliver=True)

    return ProactiveResult(
        text="Pattern learning complete",
        job_type="pattern_learn",
        deliver=False,
    )

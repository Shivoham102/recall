"""
Pattern learn — scripted analysis (runs nightly at 2am).

Counts recall_items by intent_type over the last 30 days, upserts into
user_behavior_patterns, and promotes patterns to auto_run when
frequency >= 3 AND confidence >= 0.8 (confidence = days_active / 7).

Delivers only when a pattern is newly promoted; silent otherwise.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import get_admin_db
from proactive.runner import ProactiveResult
from proactive.suggestions import upsert_suggestion

_LOOKBACK_DAYS = 30
_SHOW_THRESHOLD = 2      # visible in Profile tab
_AUTO_RUN_FREQ = 3       # minimum frequency for auto_run consideration
_AUTO_RUN_CONFIDENCE = 0.8  # minimum confidence for auto_run promotion

# Recurring-reminder detection thresholds.
_RECUR_MIN_OCCURRENCES = 4   # ≥4 reminders with the same content
_RECUR_MIN_DISTINCT_DAYS = 4  # spread across ≥4 distinct local calendar days

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _normalize(content: str) -> str:
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", (content or "").lower())).strip()


def _detect_recurring_reminders(db, user_id: str, user_tz: str, cutoff_iso: str) -> int:
    """Find one-off reminders the user keeps re-creating and write a 'recurring_reminder'
    suggestion for each. due_at is converted to the user's tz before bucketing so DST and
    timezone don't split one real habit. Returns the number of suggestions written/re-armed."""
    try:
        tz = ZoneInfo(user_tz)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")

    res = (
        db.table("recall_items")
        .select("id, content, due_at, recurrence, created_at")
        .eq("user_id", user_id)
        .gte("created_at", cutoff_iso)
        .not_.is_("due_at", "null")
        .execute()
    )
    rows = [r for r in (res.data or []) if not r.get("recurrence") and r.get("due_at")]

    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        try:
            due = datetime.fromisoformat(r["due_at"].replace("Z", "+00:00")).astimezone(tz)
        except Exception:
            continue
        norm = _normalize(r.get("content", ""))
        if norm:
            groups[norm].append((due, r["id"], r.get("content", "")))

    written = 0
    for norm, occ in groups.items():
        if len(occ) < _RECUR_MIN_OCCURRENCES:
            continue
        if len({o[0].date() for o in occ}) < _RECUR_MIN_DISTINCT_DAYS:
            continue
        modal_hour = Counter(o[0].hour for o in occ).most_common(1)[0][0]
        modal_min = Counter(o[0].minute for o in occ if o[0].hour == modal_hour).most_common(1)[0][0]
        time_str = f"{modal_hour:02d}:{modal_min:02d}"
        content = occ[-1][2]  # most recent phrasing
        recurrence = {"freq": "daily", "time": time_str, "tz": user_tz}
        payload = {
            "content": content,
            "recurrence": recurrence,
            "source_item_ids": [o[1] for o in occ],
        }
        title = f"You've set “{content}” {len(occ)} times — make it a daily reminder?"
        action = upsert_suggestion(db, user_id, "recurring_reminder", norm, title, payload)
        if action in ("inserted", "rearmed"):
            written += 1
    return written

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

    # ── Recurring-reminder + neglected-goal detection (write suggestions; surfaced via UI/brief) ──
    try:
        _detect_recurring_reminders(db, user_id, user_tz, cutoff)
    except Exception as exc:
        print(f"[pattern_learn] recurrence detection failed user={user_id}: {exc}")
    try:
        from proactive.jobs.goal_neglect import detect_neglected_goals
        detect_neglected_goals(db, user_id)
    except Exception as exc:
        print(f"[pattern_learn] goal-neglect detection failed user={user_id}: {exc}")

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

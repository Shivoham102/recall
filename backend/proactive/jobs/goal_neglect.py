"""
Goal-neglect detection — called from pattern_learn (nightly, admin-scoped).

For each active user_goal whose cadence window has elapsed since it was last surfaced,
check whether any recall_item (ANY status — a resolved "called mom" counts as acting on
the goal) created within the window semantically matches. If none match → the goal is being
neglected → write a 'neglected_goal' suggestion and stamp last_surfaced_at.

Uses get_admin_db().rpc("match_recall_items_any_status", {p_user_id}) — NOT rag.retrieve_similar,
whose anon+RLS+status='open' path returns nothing/incomplete results in a background job.
"""
from datetime import datetime, timedelta, timezone

from rag import embed
from proactive.suggestions import upsert_suggestion

# Cadence → neglect window in days (slack built in beyond the literal cadence).
_WINDOWS = {"daily": 2, "weekly": 9, "monthly": 35}
_SIMILARITY = 0.6
_MATCH_COUNT = 10


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def detect_neglected_goals(db, user_id: str) -> int:
    """Returns the number of neglected-goal suggestions written/re-armed."""
    now = datetime.now(timezone.utc)
    res = (
        db.table("user_goals")
        .select("id, goal_text, cadence_hint, last_surfaced_at")
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )
    goals = res.data or []
    written = 0

    for g in goals:
        window_days = _WINDOWS.get(g.get("cadence_hint", "weekly"), 9)
        last_surfaced = _parse_ts(g.get("last_surfaced_at"))
        if last_surfaced and (now - last_surfaced) < timedelta(days=window_days):
            continue  # don't nag more than once per cadence window

        try:
            vec = embed(g["goal_text"])
        except Exception:
            continue

        match_res = db.rpc(
            "match_recall_items_any_status",
            {"query_embedding": vec, "match_count": _MATCH_COUNT, "p_user_id": user_id},
        ).execute()

        window_start = now - timedelta(days=window_days)
        acted = False
        for m in (match_res.data or []):
            if (m.get("similarity") or 0) < _SIMILARITY:
                continue
            created = _parse_ts(m.get("created_at"))
            if created and created >= window_start:
                acted = True
                break
        if acted:
            continue

        goal_text = g["goal_text"]
        title = f"You wanted to keep up with “{goal_text}”. Nothing logged lately. Add a reminder?"
        payload = {"goal_id": g["id"], "goal_text": goal_text}
        action = upsert_suggestion(db, user_id, "neglected_goal", f"goal:{g['id']}", title, payload)
        if action in ("inserted", "rearmed"):
            written += 1
        db.table("user_goals").update({"last_surfaced_at": now.isoformat()}).eq("id", g["id"]).execute()

    return written

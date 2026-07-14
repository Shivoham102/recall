"""
Goal-neglect detection — called from pattern_learn (nightly, admin-scoped).

For each active user_goal whose adaptive re-check interval has elapsed since it was
last surfaced, check whether any recall_item (ANY status — a resolved "called mom"
counts as acting on the goal) created within the window semantically matches. If none
match → the goal is being neglected → write a 'neglected_goal' suggestion. Either way,
resample the next interval via a per-user, per-cadence-tier Thompson Sampling bandit
(goal_nudge_arms) instead of a fixed window, using accept/dismiss of the resulting
suggestion as the reward signal (NOT "was a match found" — that grows mechanically with
window size and would bias the bandit toward always picking the longest interval).

Uses get_admin_db().rpc("match_recall_items_any_status", {p_user_id}) — NOT rag.retrieve_similar,
whose anon+RLS+status='open' path returns nothing/incomplete results in a background job.
"""
import random
from datetime import datetime, timedelta, timezone

from rag import embed
from proactive.suggestions import upsert_suggestion

# Bootstrap/seed centers — used only as the fallback before a goal has ever been sampled.
_WINDOWS = {"daily": 2, "weekly": 9, "monthly": 35}

# Candidate re-check intervals (days) per cadence tier, spread around the old fixed default.
_ARM_SPREAD_DAYS = {
    "daily": [1, 2, 4, 7],
    "weekly": [4, 9, 16, 25],
    "monthly": [20, 35, 50, 70],
}

_SIMILARITY = 0.6
_MATCH_COUNT = 10


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _arms_for(db, user_id: str, cadence_hint: str) -> list[dict]:
    """Idempotently seed then return this user's 4 arm rows for cadence_hint.
    ignore_duplicates=True is load-bearing — a plain merge upsert would reset
    every arm's learned alpha/beta back to the seed values on every call."""
    intervals = _ARM_SPREAD_DAYS.get(cadence_hint, _ARM_SPREAD_DAYS["weekly"])
    rows = [
        {"user_id": user_id, "cadence_hint": cadence_hint, "interval_days": d}
        for d in intervals
    ]
    db.table("goal_nudge_arms").upsert(
        rows, on_conflict="user_id,cadence_hint,interval_days", ignore_duplicates=True
    ).execute()
    res = (
        db.table("goal_nudge_arms")
        .select("id, interval_days, alpha, beta")
        .eq("user_id", user_id)
        .eq("cadence_hint", cadence_hint)
        .execute()
    )
    return res.data or []


def _sample_interval(arms: list[dict], fallback_days: int) -> int:
    """Thompson Sampling: one random.betavariate(alpha, beta) draw per arm,
    return the interval_days of the highest draw."""
    if not arms:
        return fallback_days
    best_days = fallback_days
    best_draw = -1.0
    for arm in arms:
        draw = random.betavariate(arm.get("alpha") or 1.0, arm.get("beta") or 1.0)
        if draw > best_draw:
            best_draw = draw
            best_days = arm["interval_days"]
    return best_days


def _credit_arm(db, user_id: str, cadence_hint: str, interval_days: int, accepted: bool) -> None:
    """Read-then-increment alpha (accepted) or beta (not) on the matching arm row.
    Safe as a non-atomic read-then-write only because this job runs at most once
    per user per night (cron.py's per-(user,job_type,tz) fan-out) — no concurrent
    writers to a given arm row."""
    res = (
        db.table("goal_nudge_arms")
        .select("id, alpha, beta")
        .eq("user_id", user_id)
        .eq("cadence_hint", cadence_hint)
        .eq("interval_days", interval_days)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        return
    field = "alpha" if accepted else "beta"
    db.table("goal_nudge_arms").update({field: (row.get(field) or 1.0) + 1.0}).eq("id", row["id"]).execute()


def _get_suggestion_id(db, user_id: str, dedupe_key: str) -> str | None:
    """Fetch the id of the agent_suggestions row for this exact dedupe_key, without
    changing upsert_suggestion's return contract (pattern_learn.py's recurring_reminder
    path also depends on that contract)."""
    res = (
        db.table("agent_suggestions")
        .select("id")
        .eq("user_id", user_id)
        .eq("kind", "neglected_goal")
        .eq("dedupe_key", dedupe_key)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    return row["id"] if row else None


def _resolve_prior_feedback(db, user_id: str, goal: dict) -> None:
    """If this goal has a pending_arm_days/pending_suggestion_id from a prior cycle,
    look up that suggestion's current status, credit the arm (accepted -> alpha+=1,
    else -> beta+=1, including 'still pending' i.e. ignored), then clear both
    pending_* columns. No-op if pending_arm_days is None (new goal, or a goal that
    predates this migration).

    A suggestion still sitting at status='pending' here is superseded by this new
    cycle (a fresh, date-scoped dedupe_key is about to be considered below) — it's
    auto-expired to 'dismissed' so it stops appearing in GET /agent/suggestions and
    can't be double-accepted alongside whatever this cycle writes next. Without
    this, every cycle that finds the goal still neglected would insert another
    'pending' row under a new key, and old ones would accumulate forever."""
    pending_days = goal.get("pending_arm_days")
    suggestion_id = goal.get("pending_suggestion_id")
    if pending_days is None or not suggestion_id:
        return

    res = (
        db.table("agent_suggestions")
        .select("status")
        .eq("id", suggestion_id)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    status = row.get("status") if row else None
    accepted = status == "accepted"

    _credit_arm(db, user_id, goal.get("cadence_hint", "weekly"), pending_days, accepted)

    if status == "pending":
        # Guard on status='pending' at write time too — the read above and this
        # write aren't atomic, so a concurrent POST /agent/suggestions/{id}/accept
        # could flip it to 'accepted' in between. Without this guard, this write
        # would silently clobber that accept back to 'dismissed'.
        db.table("agent_suggestions").update({
            "status": "dismissed",
            "acted_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", suggestion_id).eq("status", "pending").execute()

    db.table("user_goals").update({
        "pending_arm_days": None,
        "pending_suggestion_id": None,
    }).eq("id", goal["id"]).execute()


def detect_neglected_goals(db, user_id: str) -> int:
    """Returns the number of neglected-goal suggestions written/re-armed."""
    now = datetime.now(timezone.utc)
    res = (
        db.table("user_goals")
        .select(
            "id, goal_text, cadence_hint, last_surfaced_at, "
            "current_interval_days, pending_arm_days, pending_suggestion_id"
        )
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )
    goals = res.data or []
    written = 0

    for g in goals:
        cadence_hint = g.get("cadence_hint", "weekly")
        interval_days = g.get("current_interval_days") or _WINDOWS.get(cadence_hint, 9)
        last_surfaced = _parse_ts(g.get("last_surfaced_at"))
        if last_surfaced and (now - last_surfaced) < timedelta(days=interval_days):
            continue  # not due yet

        try:
            _resolve_prior_feedback(db, user_id, g)
        except Exception as exc:
            print(f"[goal_neglect] arm-credit failed goal={g['id']}: {exc}")

        try:
            vec = embed(g["goal_text"])
        except Exception:
            continue

        try:
            match_res = db.rpc(
                "match_recall_items_any_status",
                {"query_embedding": vec, "match_count": _MATCH_COUNT, "p_user_id": user_id},
            ).execute()
        except Exception as exc:
            print(f"[goal_neglect] match RPC failed goal={g['id']}: {exc}")
            continue

        window_start = now - timedelta(days=interval_days)
        acted = False
        for m in (match_res.data or []):
            if (m.get("similarity") or 0) < _SIMILARITY:
                continue
            created = _parse_ts(m.get("created_at"))
            if created and created >= window_start:
                acted = True
                break

        update_fields: dict = {"last_surfaced_at": now.isoformat()}

        if not acted:
            goal_text = g["goal_text"]
            dedupe_key = f"goal:{g['id']}:{now.date().isoformat()}"
            title = f"You wanted to keep up with \"{goal_text}\". Nothing logged lately. Add a reminder?"
            payload = {"goal_id": g["id"], "goal_text": goal_text}
            action = upsert_suggestion(db, user_id, "neglected_goal", dedupe_key, title, payload)
            if action in ("inserted", "rearmed"):
                written += 1
            if action != "skipped":
                suggestion_id = _get_suggestion_id(db, user_id, dedupe_key)
                if suggestion_id:
                    update_fields["pending_arm_days"] = interval_days
                    update_fields["pending_suggestion_id"] = suggestion_id

        try:
            arms = _arms_for(db, user_id, cadence_hint)
            update_fields["current_interval_days"] = _sample_interval(
                arms, fallback_days=_WINDOWS.get(cadence_hint, 9)
            )
        except Exception as exc:
            print(f"[goal_neglect] arm sampling failed goal={g['id']}: {exc}")

        db.table("user_goals").update(update_fields).eq("id", g["id"]).execute()

    return written

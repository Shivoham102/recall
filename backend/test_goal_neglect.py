"""
Standalone test for adaptive goal-nudge cadence (Thompson Sampling bandit + the
date-scoped dedupe_key fix for suggestions that never resurfaced after being
accepted once). No pytest. Run from the backend directory:

    python test_goal_neglect.py

Requires SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY in .env
(service role needed to bypass RLS for test data setup), and the goal_nudge_arms
table + user_goals.current_interval_days/pending_arm_days/pending_suggestion_id
columns from db/schema.sql already applied to that Supabase project.
"""
import sys
import os
import random
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    print("ERROR: SUPABASE_SERVICE_ROLE_KEY not set — needed to bypass RLS for test setup")
    sys.exit(1)

# ── Patch get_db to return admin client before importing routes ───────────────
import db as _db_module
_admin = _db_module.get_admin_db()
_db_module._client = _admin

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import proactive.jobs.goal_neglect as goal_neglect

_PREFIX = "test-recall-goal-neglect-"


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _CannedExecute:
    def __init__(self, data):
        self.data = data


class _CannedRPC:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _CannedExecute(self._data)


class _FakeDB:
    """Forwards .table(...) to the real admin client so Postgres reads/writes are
    genuinely exercised, but intercepts the semantic-match RPC with canned data so
    tests don't depend on real OpenAI embeddings / pgvector similarity."""

    def __init__(self, matches: list | None = None):
        self.matches = matches or []

    def table(self, name):
        return _admin.table(name)

    def rpc(self, name, params):
        if name == "match_recall_items_any_status":
            return _CannedRPC(self.matches)
        return _admin.rpc(name, params)


class _FrozenDatetime(datetime):
    _frozen = None

    @classmethod
    def now(cls, tz=None):
        frozen = cls._frozen
        return frozen.astimezone(tz) if tz else frozen


def _freeze(fixed_dt):
    _FrozenDatetime._frozen = fixed_dt
    return patch.object(goal_neglect, "datetime", _FrozenDatetime)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_id(tag: str) -> str:
    return f"{_PREFIX}{tag}"


def _setup_user(uid: str):
    _admin.table("users").upsert({"id": uid, "email": f"{uid}@recall.test"}).execute()


def _cleanup_user(uid: str):
    _admin.table("agent_suggestions").delete().eq("user_id", uid).execute()
    _admin.table("goal_nudge_arms").delete().eq("user_id", uid).execute()
    _admin.table("user_goals").delete().eq("user_id", uid).execute()
    _admin.table("recall_items").delete().eq("user_id", uid).execute()
    _admin.table("users").delete().eq("id", uid).execute()


def _create_goal(uid: str, cadence_hint: str = "weekly", goal_text: str = "test goal", **overrides) -> dict:
    row = {"user_id": uid, "goal_text": goal_text, "cadence_hint": cadence_hint, "status": "active"}
    row.update(overrides)
    return _admin.table("user_goals").insert(row).execute().data[0]


def _get_goal(goal_id: str) -> dict:
    return _admin.table("user_goals").select("*").eq("id", goal_id).execute().data[0]


def _get_suggestions(uid: str, goal_id: str) -> list[dict]:
    res = (
        _admin.table("agent_suggestions")
        .select("*")
        .eq("user_id", uid)
        .eq("kind", "neglected_goal")
        .execute()
    )
    return [s for s in (res.data or []) if (s.get("payload") or {}).get("goal_id") == goal_id]


def _get_arms(uid: str, cadence_hint: str) -> list[dict]:
    res = (
        _admin.table("goal_nudge_arms")
        .select("*")
        .eq("user_id", uid)
        .eq("cadence_hint", cadence_hint)
        .execute()
    )
    return res.data or []


def _seed_and_get_arm(uid: str, cadence_hint: str, interval_days: int) -> dict:
    goal_neglect._arms_for(_FakeDB(), uid, cadence_hint)
    return next(a for a in _get_arms(uid, cadence_hint) if a["interval_days"] == interval_days)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_first_ever_check_bootstraps_default_interval(uid):
    goal = _create_goal(uid, cadence_hint="weekly")
    db = _FakeDB(matches=[])
    written = goal_neglect.detect_neglected_goals(db, uid)
    assert written == 1

    g = _get_goal(goal["id"])
    assert g["pending_arm_days"] == 9, f"expected bootstrap to the weekly default (9), got {g['pending_arm_days']}"
    assert g["current_interval_days"] in (4, 9, 16, 25)

    suggestions = _get_suggestions(uid, goal["id"])
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "pending"
    assert suggestions[0]["dedupe_key"].startswith(f"goal:{goal['id']}:")


def test_acted_true_still_stamps_and_resamples(uid):
    goal = _create_goal(uid, cadence_hint="weekly")
    old_surfaced = datetime.now(timezone.utc) - timedelta(days=20)
    _admin.table("user_goals").update({
        "last_surfaced_at": old_surfaced.isoformat(),
        "current_interval_days": 9,
    }).eq("id", goal["id"]).execute()

    recent_match = datetime.now(timezone.utc) - timedelta(days=1)
    db = _FakeDB(matches=[{"similarity": 0.9, "created_at": recent_match.isoformat()}])
    written = goal_neglect.detect_neglected_goals(db, uid)
    assert written == 0

    g = _get_goal(goal["id"])
    new_surfaced = datetime.fromisoformat(g["last_surfaced_at"].replace("Z", "+00:00"))
    assert new_surfaced > old_surfaced, "last_surfaced_at must advance even when the goal is not neglected"
    assert g["current_interval_days"] is not None
    assert g["pending_arm_days"] is None, "no suggestion was shown, nothing should be pending"
    assert len(_get_suggestions(uid, goal["id"])) == 0


def test_accepted_suggestion_gets_re_surfaced_next_cycle(uid):
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    goal = _create_goal(uid, cadence_hint="weekly")

    with _freeze(day1):
        goal_neglect.detect_neglected_goals(_FakeDB(matches=[]), uid)

    suggestions1 = _get_suggestions(uid, goal["id"])
    assert len(suggestions1) == 1, f"expected 1 suggestion after cycle 1, got {len(suggestions1)}"
    sugg1 = suggestions1[0]
    dedupe1 = sugg1["dedupe_key"]
    assert dedupe1 == f"goal:{goal['id']}:{day1.date().isoformat()}"

    # user accepts it
    _admin.table("agent_suggestions").update({
        "status": "accepted", "acted_at": day1.isoformat(),
    }).eq("id", sugg1["id"]).execute()

    # force due again regardless of whichever interval got sampled, and move the
    # calendar date forward so cycle 2 gets a different dedupe_key
    _admin.table("user_goals").update({
        "last_surfaced_at": (day1 - timedelta(days=100)).isoformat(),
    }).eq("id", goal["id"]).execute()
    day2 = day1 + timedelta(days=40)

    with _freeze(day2):
        goal_neglect.detect_neglected_goals(_FakeDB(matches=[]), uid)

    suggestions2 = _get_suggestions(uid, goal["id"])
    assert len(suggestions2) == 2, f"expected a NEW suggestion on cycle 2, got {len(suggestions2)} total"
    new_ones = [s for s in suggestions2 if s["id"] != sugg1["id"]]
    assert len(new_ones) == 1
    sugg2 = new_ones[0]
    assert sugg2["status"] == "pending", "cycle 2's suggestion must be pending, not blocked by cycle 1's acceptance"
    assert sugg2["dedupe_key"] != dedupe1, "dedupe_key must be date-scoped so it differs across cycles"

    # the bootstrap arm (9 days) that cycle 1 tested should now be credited for the acceptance
    arm9 = next(a for a in _get_arms(uid, "weekly") if a["interval_days"] == 9)
    assert arm9["alpha"] > 1.0, "accepted suggestion should have credited alpha on the tested arm"


def test_stale_pending_suggestion_gets_auto_dismissed_next_cycle(uid):
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    goal = _create_goal(uid, cadence_hint="weekly")

    with _freeze(day1):
        goal_neglect.detect_neglected_goals(_FakeDB(matches=[]), uid)

    suggestions1 = _get_suggestions(uid, goal["id"])
    assert len(suggestions1) == 1
    sugg1 = suggestions1[0]
    assert sugg1["status"] == "pending"

    # user never responds; force due again and roll the calendar forward
    _admin.table("user_goals").update({
        "last_surfaced_at": (day1 - timedelta(days=100)).isoformat(),
    }).eq("id", goal["id"]).execute()
    day2 = day1 + timedelta(days=40)

    with _freeze(day2):
        goal_neglect.detect_neglected_goals(_FakeDB(matches=[]), uid)

    sugg1_after = _admin.table("agent_suggestions").select("*").eq("id", sugg1["id"]).execute().data[0]
    assert sugg1_after["status"] == "dismissed", "un-actioned suggestion must be auto-expired once superseded"

    suggestions2 = _get_suggestions(uid, goal["id"])
    pending_now = [s for s in suggestions2 if s["status"] == "pending"]
    assert len(pending_now) == 1, f"exactly one pending suggestion should exist for this goal, got {len(pending_now)}"

    arm9 = next(a for a in _get_arms(uid, "weekly") if a["interval_days"] == 9)
    assert arm9["beta"] > 1.0, "an ignored suggestion should have credited beta on the tested arm"


def test_prior_accepted_credits_alpha(uid):
    goal = _create_goal(uid, cadence_hint="weekly")
    arm_before = _seed_and_get_arm(uid, "weekly", 9)
    sugg = _admin.table("agent_suggestions").insert({
        "user_id": uid, "kind": "neglected_goal", "status": "accepted",
        "title": "t", "payload": {"goal_id": goal["id"]}, "dedupe_key": f"goal:{goal['id']}:manual",
    }).execute().data[0]

    goal_row = {**goal, "pending_arm_days": 9, "pending_suggestion_id": sugg["id"]}
    goal_neglect._resolve_prior_feedback(_FakeDB(), uid, goal_row)

    arm_after = next(a for a in _get_arms(uid, "weekly") if a["interval_days"] == 9)
    assert arm_after["alpha"] == arm_before["alpha"] + 1.0
    assert arm_after["beta"] == arm_before["beta"]

    g = _get_goal(goal["id"])
    assert g["pending_arm_days"] is None
    assert g["pending_suggestion_id"] is None


def test_prior_dismissed_credits_beta(uid):
    goal = _create_goal(uid, cadence_hint="weekly")
    arm_before = _seed_and_get_arm(uid, "weekly", 16)
    sugg = _admin.table("agent_suggestions").insert({
        "user_id": uid, "kind": "neglected_goal", "status": "dismissed",
        "title": "t", "payload": {"goal_id": goal["id"]}, "dedupe_key": f"goal:{goal['id']}:manual",
        "acted_at": datetime.now(timezone.utc).isoformat(),
    }).execute().data[0]

    goal_row = {**goal, "pending_arm_days": 16, "pending_suggestion_id": sugg["id"]}
    goal_neglect._resolve_prior_feedback(_FakeDB(), uid, goal_row)

    arm_after = next(a for a in _get_arms(uid, "weekly") if a["interval_days"] == 16)
    assert arm_after["beta"] == arm_before["beta"] + 1.0
    assert arm_after["alpha"] == arm_before["alpha"]


def test_prior_still_pending_counts_as_failure(uid):
    goal = _create_goal(uid, cadence_hint="weekly")
    arm_before = _seed_and_get_arm(uid, "weekly", 25)
    sugg = _admin.table("agent_suggestions").insert({
        "user_id": uid, "kind": "neglected_goal", "status": "pending",
        "title": "t", "payload": {"goal_id": goal["id"]}, "dedupe_key": f"goal:{goal['id']}:manual",
    }).execute().data[0]

    goal_row = {**goal, "pending_arm_days": 25, "pending_suggestion_id": sugg["id"]}
    goal_neglect._resolve_prior_feedback(_FakeDB(), uid, goal_row)

    arm_after = next(a for a in _get_arms(uid, "weekly") if a["interval_days"] == 25)
    assert arm_after["beta"] == arm_before["beta"] + 1.0, "an un-actioned suggestion should count as a failure"


def test_arms_seeded_idempotently_preserves_learned_state(uid):
    db = _FakeDB()
    arms1 = goal_neglect._arms_for(db, uid, "monthly")
    assert len(arms1) == 4

    target = arms1[0]
    _admin.table("goal_nudge_arms").update({"alpha": target["alpha"] + 5}).eq("id", target["id"]).execute()

    arms2 = goal_neglect._arms_for(db, uid, "monthly")
    assert len(arms2) == 4
    bumped = next(a for a in arms2 if a["id"] == target["id"])
    assert bumped["alpha"] == target["alpha"] + 5, "re-seeding must not reset learned alpha/beta"


def test_sample_interval_prefers_high_alpha_arm_statistically(uid):
    random.seed(42)
    arms = [
        {"interval_days": 4, "alpha": 1.0, "beta": 1.0},
        {"interval_days": 9, "alpha": 50.0, "beta": 1.0},
        {"interval_days": 16, "alpha": 1.0, "beta": 1.0},
        {"interval_days": 25, "alpha": 1.0, "beta": 1.0},
    ]
    counts: dict = {}
    for _ in range(500):
        picked = goal_neglect._sample_interval(arms, fallback_days=9)
        counts[picked] = counts.get(picked, 0) + 1
    assert counts.get(9, 0) > 400, f"expected the strong (alpha=50) arm to dominate, got {counts}"


def test_pooled_across_goals_same_cadence(uid):
    goal_a = _create_goal(uid, cadence_hint="weekly", goal_text="goal a")
    goal_b = _create_goal(uid, cadence_hint="weekly", goal_text="goal b")
    goal_neglect.detect_neglected_goals(_FakeDB(matches=[]), uid)

    arms = _get_arms(uid, "weekly")
    assert len(arms) == 4, f"expected 4 shared arms across 2 goals, got {len(arms)}"

    ga = _get_goal(goal_a["id"])
    gb = _get_goal(goal_b["id"])
    assert ga["pending_arm_days"] is not None
    assert gb["pending_arm_days"] is not None


def test_pre_migration_goal_with_null_columns_behaves_like_bootstrap(uid):
    goal = _create_goal(uid, cadence_hint="monthly")
    _admin.table("user_goals").update({
        "last_surfaced_at": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
    }).eq("id", goal["id"]).execute()

    goal_neglect.detect_neglected_goals(_FakeDB(matches=[]), uid)  # must not raise on NULL columns

    g = _get_goal(goal["id"])
    assert g["current_interval_days"] in (20, 35, 50, 70)
    assert g["pending_arm_days"] == 35


TESTS = [
    test_first_ever_check_bootstraps_default_interval,
    test_acted_true_still_stamps_and_resamples,
    test_accepted_suggestion_gets_re_surfaced_next_cycle,
    test_stale_pending_suggestion_gets_auto_dismissed_next_cycle,
    test_prior_accepted_credits_alpha,
    test_prior_dismissed_credits_beta,
    test_prior_still_pending_counts_as_failure,
    test_arms_seeded_idempotently_preserves_learned_state,
    test_sample_interval_prefers_high_alpha_arm_statistically,
    test_pooled_across_goals_same_cadence,
    test_pre_migration_goal_with_null_columns_behaves_like_bootstrap,
]


def _run(test):
    uid = _user_id(test.__name__)
    _cleanup_user(uid)  # in case a previous failed run left rows behind
    _setup_user(uid)
    try:
        with patch.object(goal_neglect, "embed", return_value=[0.0] * 8):
            test(uid)
    finally:
        _cleanup_user(uid)


if __name__ == "__main__":
    passed = failed = 0
    for test in TESTS:
        try:
            _run(test)
            print(f"  PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

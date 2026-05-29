"""
Standalone test for reminder status-filter and mark-missed logic.
No pytest required. Run from the backend directory:

    python test_reminders.py

Requires SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY in .env
(service role needed to bypass RLS for test data setup).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    print("ERROR: SUPABASE_SERVICE_ROLE_KEY not set — needed to bypass RLS for test setup")
    sys.exit(1)

# ── Patch get_db to return admin client before importing routes ───────────────
import db as _db_module
_admin = _db_module.get_admin_db()
_db_module._client = _admin  # get_db() will return admin client; bypasses RLS

from datetime import datetime, timedelta, timezone
from routes.reminders import mark_missed, get_pending_reminders, dismiss_reminders, DismissRequest

TEST_USER_ID = "test-recall-reminders-00000000"
TEST_USER    = {"sub": TEST_USER_ID}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup_user():
    _admin.table("users").upsert({
        "id": TEST_USER_ID,
        "email": "test-reminders@recall.test",
    }).execute()

def _cleanup():
    _admin.table("recall_items").delete().eq("user_id", TEST_USER_ID).execute()

def _teardown_user():
    _cleanup()
    _admin.table("users").delete().eq("id", TEST_USER_ID).execute()

def _insert(due_offset_hours: float, status: str = "open", reminded_at=None, recurrence=None) -> str:
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    row = {
        "content": f"test item due_offset={due_offset_hours}h status={status}",
        "status": status,
        "due_at": due.isoformat(),
        "user_id": TEST_USER_ID,
    }
    if reminded_at is not None:
        row["reminded_at"] = reminded_at
    if recurrence is not None:
        row["recurrence"] = recurrence
    result = _admin.table("recall_items").insert(row).execute()
    return result.data[0]["id"]

def _row(id_: str) -> dict:
    return _admin.table("recall_items").select("*").eq("id", id_).execute().data[0]


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_pending_excludes_resolved():
    id_ = _insert(1, status="resolved")
    items = get_pending_reminders(user=TEST_USER)
    ids = [i["id"] for i in items]
    assert id_ not in ids, f"resolved item {id_} leaked into pending"

def test_pending_excludes_missed():
    id_ = _insert(1, status="missed")
    items = get_pending_reminders(user=TEST_USER)
    ids = [i["id"] for i in items]
    assert id_ not in ids, f"missed item {id_} leaked into pending"

def test_pending_includes_open_future():
    id_ = _insert(1, status="open")
    items = get_pending_reminders(user=TEST_USER)
    ids = [i["id"] for i in items]
    assert id_ in ids, f"open future item {id_} missing from pending"

def test_mark_missed_marks_overdue_open_items():
    id_ = _insert(-3, status="open")  # 3h overdue
    result = mark_missed(user=TEST_USER)
    marked_ids = [i["id"] for i in result["items"]]
    assert id_ in marked_ids, f"expected {id_} in marked items, got {marked_ids}"
    row = _row(id_)
    assert row["status"] == "missed", f"expected status=missed, got {row['status']}"
    assert row["reminded_at"] is not None, "reminded_at should be set after mark_missed"

def test_mark_missed_ignores_resolved():
    id_ = _insert(-3, status="resolved")
    result = mark_missed(user=TEST_USER)
    marked_ids = [i["id"] for i in result["items"]]
    assert id_ not in marked_ids, "resolved item should not be marked missed"
    assert _row(id_)["status"] == "resolved"

def test_mark_missed_ignores_future_items():
    id_ = _insert(2, status="open")  # 2h in the future
    result = mark_missed(user=TEST_USER)
    marked_ids = [i["id"] for i in result["items"]]
    assert id_ not in marked_ids, "future item should not be marked missed"
    assert _row(id_)["status"] == "open"

def test_mark_missed_ignores_already_reminded():
    reminded = datetime.now(timezone.utc).isoformat()
    id_ = _insert(-3, status="open", reminded_at=reminded)
    result = mark_missed(user=TEST_USER)
    marked_ids = [i["id"] for i in result["items"]]
    assert id_ not in marked_ids, "already-reminded item should not be re-marked"

def test_mark_missed_within_2h_window_ignored():
    id_ = _insert(-1, status="open")  # only 1h overdue — inside 2h window
    result = mark_missed(user=TEST_USER)
    marked_ids = [i["id"] for i in result["items"]]
    assert id_ not in marked_ids, "item < 2h overdue should not be marked missed"
    assert _row(id_)["status"] == "open"

def test_mark_missed_ignores_recurring():
    # Recurring reminders are owned solely by /reminders/due (fire-late + roll-forward).
    # mark-missed must never mark them missed, regardless of how overdue.
    rec = {"freq": "daily", "time": "06:00", "tz": "UTC"}
    id_ = _insert(-5, status="open", recurrence=rec)  # 5h overdue
    result = mark_missed(user=TEST_USER)
    marked_ids = [i["id"] for i in result["items"]]
    assert id_ not in marked_ids, "recurring item must not be marked missed"
    assert _row(id_)["status"] == "open", "recurring item status must stay open"


def test_mark_missed_cannot_affect_other_users():
    other = "test-recall-other-00000000"
    _admin.table("users").upsert({"id": other, "email": "other@recall.test"}).execute()
    due = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    other_id = _admin.table("recall_items").insert({
        "content": "other user item",
        "status": "open",
        "due_at": due,
        "user_id": other,
    }).execute().data[0]["id"]
    try:
        result = mark_missed(user=TEST_USER)
        marked_ids = [i["id"] for i in result["items"]]
        assert other_id not in marked_ids, "must not mark another user's items"
        assert _row(other_id)["status"] == "open"
    finally:
        _admin.table("recall_items").delete().eq("user_id", other).execute()
        _admin.table("users").delete().eq("id", other).execute()


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_pending_excludes_resolved,
    test_pending_excludes_missed,
    test_pending_includes_open_future,
    test_mark_missed_marks_overdue_open_items,
    test_mark_missed_ignores_resolved,
    test_mark_missed_ignores_future_items,
    test_mark_missed_ignores_already_reminded,
    test_mark_missed_within_2h_window_ignored,
    test_mark_missed_ignores_recurring,
    test_mark_missed_cannot_affect_other_users,
]

if __name__ == "__main__":
    print("Setting up test user...")
    _setup_user()

    passed = failed = 0
    for test in TESTS:
        _cleanup()
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {e}")
            failed += 1
        finally:
            _cleanup()

    print(f"\nTearing down test user...")
    _teardown_user()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

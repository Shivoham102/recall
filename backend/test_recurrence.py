"""
Offline unit tests for recurrence math (no DB / secrets needed). Run from backend/:

    python test_recurrence.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from time_utils import next_occurrence, recurrence_due_hint

LA = ZoneInfo("America/Los_Angeles")


def _p(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_daily_advances_by_one_day():
    rec = {"freq": "daily", "time": "06:00", "tz": "UTC"}
    after = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
    n1 = _p(next_occurrence(rec, after))
    assert n1 > after, "must be strictly future"
    assert n1.astimezone(timezone.utc).hour == 6
    n2 = _p(next_occurrence(rec, n1))
    assert n2 - n1 == timedelta(days=1), f"daily should advance 1 day, got {n2 - n1}"


def test_daily_local_time_preserved():
    rec = {"freq": "daily", "time": "18:00", "tz": "America/Los_Angeles"}
    after = datetime(2026, 5, 29, 10, 0, tzinfo=timezone.utc)  # 03:00 LA
    nxt = _p(next_occurrence(rec, after)).astimezone(LA)
    assert (nxt.hour, nxt.minute) == (18, 0), f"expected 18:00 local, got {nxt.hour}:{nxt.minute}"


def test_weekdays_never_weekend():
    rec = {"freq": "weekdays", "time": "09:00", "tz": "UTC"}
    # Probe several start days; result must always land Mon–Fri and be strictly future.
    for offset in range(0, 9):
        after = datetime(2026, 5, 29, 23, 0, tzinfo=timezone.utc) + timedelta(days=offset)
        nxt = _p(next_occurrence(rec, after))
        assert nxt > after
        assert nxt.weekday() < 5, f"weekdays produced weekend day {nxt.weekday()}"


def test_weekly_specific_days():
    rec = {"freq": "weekly", "time": "10:00", "days": [0, 2, 4], "tz": "UTC"}  # Mon/Wed/Fri
    after = datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc)
    nxt = _p(next_occurrence(rec, after))
    assert nxt > after
    assert nxt.weekday() in (0, 2, 4), f"got weekday {nxt.weekday()}"


def test_weekly_empty_days_falls_back_to_daily():
    rec = {"freq": "weekly", "time": "10:00", "days": [], "tz": "UTC"}
    after = datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc)
    n1 = _p(next_occurrence(rec, after))
    n2 = _p(next_occurrence(rec, n1))
    assert n2 - n1 == timedelta(days=1), "empty weekly days should behave daily"


def test_dst_spring_forward_gap():
    # 2026-03-08 02:00 LA → 03:00 (02:30 is imaginary). Must still yield a valid future instant
    # and consecutive calls must advance (no double/zero fire).
    rec = {"freq": "daily", "time": "02:30", "tz": "America/Los_Angeles"}
    after = datetime(2026, 3, 8, 1, 0, tzinfo=LA)
    n1 = _p(next_occurrence(rec, after))
    assert n1.tzinfo is not None
    assert n1 > after.astimezone(timezone.utc)
    n2 = _p(next_occurrence(rec, n1))
    assert n2 > n1


def test_dst_fall_back_fold():
    # 2026-11-01 01:30 LA is ambiguous (fall back). fold=0 → earlier instant. Must advance cleanly.
    rec = {"freq": "daily", "time": "01:30", "tz": "America/Los_Angeles"}
    after = datetime(2026, 11, 1, 0, 0, tzinfo=LA)
    n1 = _p(next_occurrence(rec, after))
    assert n1 > after.astimezone(timezone.utc)
    n2 = _p(next_occurrence(rec, n1))
    assert n2 > n1


def test_invalid_time_raises():
    for bad in ("25:00", "12:99", "noon", "", "1800"):
        try:
            next_occurrence({"freq": "daily", "time": bad, "tz": "UTC"}, datetime.now(timezone.utc))
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for time={bad!r}")


def test_bad_tz_falls_back_utc():
    rec = {"freq": "daily", "time": "08:00", "tz": "Not/AReal_Zone"}
    nxt = _p(next_occurrence(rec, datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc)))
    assert nxt.astimezone(timezone.utc).hour == 8


def test_due_hint_strings():
    assert recurrence_due_hint({"freq": "daily", "time": "18:00"}) == "every day at 18:00"
    assert recurrence_due_hint({"freq": "weekdays", "time": "09:00"}) == "weekdays at 09:00"
    assert recurrence_due_hint({"freq": "weekly", "time": "10:00"}) == "weekly at 10:00"


TESTS = [
    test_daily_advances_by_one_day,
    test_daily_local_time_preserved,
    test_weekdays_never_weekend,
    test_weekly_specific_days,
    test_weekly_empty_days_falls_back_to_daily,
    test_dst_spring_forward_gap,
    test_dst_fall_back_fold,
    test_invalid_time_raises,
    test_bad_tz_falls_back_utc,
    test_due_hint_strings,
]

if __name__ == "__main__":
    passed = failed = 0
    for test in TESTS:
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
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

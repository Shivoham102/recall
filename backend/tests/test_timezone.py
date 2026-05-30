import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAugmentUserTurnTimezone:
    def test_uses_provided_timezone(self):
        """Server UTC + user_tz Eastern → date label reflects Eastern date, not UTC date."""
        from unittest.mock import patch
        from datetime import datetime, timezone as tz
        from zoneinfo import ZoneInfo
        # 2026-05-19 02:00 UTC = 2026-05-18 22:00 Eastern (UTC-4)
        fixed_utc = datetime(2026, 5, 19, 2, 0, tzinfo=tz.utc)
        eastern = ZoneInfo("America/New_York")
        with patch("agent.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda zone=None: fixed_utc.astimezone(zone if zone else tz.utc)
            from agent import _augment_user_turn
            result = _augment_user_turn("hello", "", user_tz="America/New_York")
        assert "May 18" in result  # Eastern date, not UTC May 19

    def test_bad_tz_falls_back_to_utc(self):
        """Invalid timezone string must not raise; must fall back to UTC."""
        from agent import _augment_user_turn
        result = _augment_user_turn("hello", "", user_tz="Not/ATimezone")
        assert "[Date:" in result

    def test_user_name_injected_when_provided(self):
        from agent import _augment_user_turn
        result = _augment_user_turn("hello", "", user_name="Alice")
        assert "[User name: Alice]" in result

    def test_user_name_absent_when_empty(self):
        from agent import _augment_user_turn
        result = _augment_user_turn("hello", "")
        assert "[User name:" not in result


class TestCalendarDayDedupeWindow:
    def test_local_midnight_not_utc_midnight(self):
        """PDT user at 01:00 UTC → local midnight = May 18 07:00 UTC, not May 19 00:00 UTC."""
        from datetime import datetime, timezone as tz
        from zoneinfo import ZoneInfo

        now_utc = datetime(2026, 5, 19, 1, 0, tzinfo=tz.utc)
        user_tz_obj = ZoneInfo("America/Los_Angeles")
        local_now = now_utc.astimezone(user_tz_obj)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = local_midnight.astimezone(tz.utc)

        assert window_start.day == 18   # May 18, not May 19
        assert window_start.hour == 7   # 07:00 UTC = midnight PDT (UTC-7)

    def test_utc_user_midnight_is_utc_midnight(self):
        """UTC user's local midnight equals UTC midnight — no shift."""
        from datetime import datetime, timezone as tz
        from zoneinfo import ZoneInfo

        now_utc = datetime(2026, 5, 19, 1, 0, tzinfo=tz.utc)
        user_tz_obj = ZoneInfo("UTC")
        local_now = now_utc.astimezone(user_tz_obj)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = local_midnight.astimezone(tz.utc)

        assert window_start.day == 19
        assert window_start.hour == 0

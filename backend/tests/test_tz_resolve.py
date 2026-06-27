import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from time_utils import resolve_timezone


def test_prefers_first_valid_candidate():
    # Request-supplied tz wins when valid.
    assert resolve_timezone("America/New_York", "Europe/London") == "America/New_York"


def test_falls_back_to_stored_when_request_invalid():
    # Invalid/missing request tz must fall through to the user's stored tz, NOT straight to UTC.
    assert resolve_timezone("Not/AZone", "America/Los_Angeles") == "America/Los_Angeles"
    assert resolve_timezone(None, "America/Los_Angeles") == "America/Los_Angeles"
    assert resolve_timezone("", "America/Los_Angeles") == "America/Los_Angeles"


def test_utc_only_when_no_valid_candidate():
    assert resolve_timezone(None, None) == "UTC"
    assert resolve_timezone("Not/AZone", "Also/Bogus") == "UTC"
    assert resolve_timezone() == "UTC"

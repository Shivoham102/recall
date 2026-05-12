import dateparser
import zoneinfo
from datetime import timezone as _tz


def _is_valid_iana(tz: str) -> bool:
    try:
        zoneinfo.ZoneInfo(tz)
        return True
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return False


def parse_due_at(due_hint: str | None, timezone: str = "UTC") -> str | None:
    if not due_hint:
        return None
    if not _is_valid_iana(timezone):
        timezone = "UTC"
    parsed = dateparser.parse(
        due_hint,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": timezone,
        },
    )
    if not parsed:
        return None
    return parsed.astimezone(_tz.utc).isoformat()

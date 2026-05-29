import re
import dateparser
import zoneinfo
from datetime import datetime, time as _time, timedelta, timezone as _tz

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _is_valid_iana(tz: str) -> bool:
    try:
        zoneinfo.ZoneInfo(tz)
        return True
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return False


def recurrence_due_hint(recurrence: dict) -> str:
    """Human-ish due_hint string for a recurring item, so the Tasks/Reminders split (which keys
    off due_hint being non-null) always classifies recurring items as reminders."""
    freq = recurrence.get("freq", "daily")
    t = recurrence.get("time", "")
    if freq == "weekdays":
        return f"weekdays at {t}"
    if freq == "weekly":
        return f"weekly at {t}"
    return f"every day at {t}"


def next_occurrence(recurrence: dict, after: datetime) -> str:
    """Compute the next recurring fire time strictly after `after`, as a UTC ISO string.

    recurrence shape:
      { "freq": "daily"|"weekdays"|"weekly", "time": "HH:MM", "days": [0..6], "tz": "<IANA>" }
    days: weekly only, 0=Mon..6=Sun (matches datetime.weekday()).

    Edge handling:
      - weekly with empty/missing days → behaves like daily.
      - searches forward day-by-day, cap 366 days; raises ValueError if nothing matches.
      - DST gap/fold: relies on zoneinfo (fold=0 → earlier instant for ambiguous fall-back times;
        imaginary spring-forward times still yield a valid instant). The returned value is always
        a single strictly-future UTC instant.
    """
    tz_name = recurrence.get("tz") or "UTC"
    if not _is_valid_iana(tz_name):
        tz_name = "UTC"
    tz = zoneinfo.ZoneInfo(tz_name)

    time_str = str(recurrence.get("time", "")).strip()
    m = _TIME_RE.match(time_str)
    if not m:
        raise ValueError(f"Invalid recurrence time: {recurrence.get('time')!r}")
    hour, minute = int(m.group(1)), int(m.group(2))

    freq = recurrence.get("freq", "daily")
    days = recurrence.get("days") or []
    weekly = freq == "weekly" and bool(days)

    if after.tzinfo is None:
        after = after.replace(tzinfo=_tz.utc)
    start_date = after.astimezone(tz).date()

    for offset in range(0, 367):
        cand_date = start_date + timedelta(days=offset)
        weekday = cand_date.weekday()  # 0=Mon..6=Sun
        if freq == "weekdays" and weekday >= 5:
            continue
        if weekly and weekday not in days:
            continue
        candidate = datetime.combine(cand_date, _time(hour, minute), tzinfo=tz)
        if candidate > after:
            return candidate.astimezone(_tz.utc).isoformat()

    raise ValueError("No recurrence occurrence found within 366 days")


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

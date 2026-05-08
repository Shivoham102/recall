import asyncio
import base64
import email.utils
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import pathlib
import re
import sys

from googleapiclient.discovery import build
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from google_auth import get_credentials, get_credentials_for_user
import context
from db import get_admin_db

_CHECKIN_FILE = pathlib.Path(__file__).parent.parent / "last_checkin.json"
_STYLE_PROFILE_TABLE = "email_style_profiles"
_STYLE_EVENT_TABLE = "email_style_events"
_STYLE_SAMPLE_TARGET = 10
_STYLE_STALE_DAYS = 8

_GREETING_RE = re.compile(r"^\s*(hi|hello|hey|dear)\b[^\n]{0,100}", re.IGNORECASE)
_FORMAL_WORDS = {"regards", "sincerely", "appreciate", "pleased", "kindly", "thank you"}
_CASUAL_WORDS = {"hey", "thanks", "quick", "awesome", "yep", "no worries"}
_CLOSING_CANDIDATES = ("thanks", "thank you", "best", "regards", "cheers", "sincerely")

# Sender patterns that indicate automated/newsletter email — skip these
_NOISE_PATTERNS = re.compile(
    r"(noreply|no-reply|donotreply|do-not-reply|notification|newsletter"
    r"|mailer-daemon|postmaster|bounce|alerts?@|updates?@|support@"
    r"|unsubscribe|marketing|digest|automated)",
    re.IGNORECASE,
)


def _get_creds():
    user_id = context.current_user_id.get("")
    if user_id:
        return get_credentials_for_user(user_id)
    return get_credentials()


def _gmail_service():
    return build("gmail", "v1", credentials=_get_creds(), cache_discovery=False)


def _calendar_service():
    return build("calendar", "v3", credentials=_get_creds(), cache_discovery=False)


def _gmail_service_for_user(user_id: str):
    creds = get_credentials_for_user(user_id)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _style_is_stale(ts: str | None) -> bool:
    if not ts:
        return True
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        parsed_utc = _to_utc(parsed)
        if parsed_utc is None:
            return True
        return datetime.now(timezone.utc) - parsed_utc > timedelta(days=_STYLE_STALE_DAYS)
    except Exception:
        return True


def _extract_closing(text: str) -> str:
    tail = [ln.strip() for ln in text.splitlines()[-6:] if ln.strip()]
    for line in reversed(tail):
        lowered = line.lower()
        if any(token in lowered for token in _CLOSING_CANDIDATES):
            return line
    return ""


def _build_style_features(samples: list[str]) -> dict:
    if not samples:
        return {
            "sample_count": 0,
            "avg_words_per_sentence": 0,
            "avg_char_count": 0,
            "greeting_patterns": [],
            "closing_patterns": [],
            "formality": "balanced",
        }

    greetings: list[str] = []
    closings: list[str] = []
    sentence_lengths: list[int] = []
    char_counts: list[int] = []
    formal_hits = 0
    casual_hits = 0

    for sample in samples:
        body = sample.strip()
        if not body:
            continue
        char_counts.append(len(body))

        greeting_match = _GREETING_RE.search(body)
        if greeting_match:
            greetings.append(greeting_match.group(0).strip())

        closing = _extract_closing(body)
        if closing:
            closings.append(closing)

        words = re.findall(r"[A-Za-z']+", body.lower())
        formal_hits += sum(1 for token in words if token in _FORMAL_WORDS)
        casual_hits += sum(1 for token in words if token in _CASUAL_WORDS)

        for sentence in re.split(r"[.!?]+", body):
            sentence_words = re.findall(r"[A-Za-z']+", sentence)
            if sentence_words:
                sentence_lengths.append(len(sentence_words))

    if formal_hits > casual_hits * 1.2:
        formality = "formal"
    elif casual_hits > formal_hits * 1.2:
        formality = "casual"
    else:
        formality = "balanced"

    greeting_counts = [pattern for pattern, _ in Counter(greetings).most_common(3)]
    closing_counts = [pattern for pattern, _ in Counter(closings).most_common(3)]
    avg_words_per_sentence = round(sum(sentence_lengths) / len(sentence_lengths), 1) if sentence_lengths else 0
    avg_char_count = round(sum(char_counts) / len(char_counts), 1) if char_counts else 0

    return {
        "sample_count": len(samples),
        "avg_words_per_sentence": avg_words_per_sentence,
        "avg_char_count": avg_char_count,
        "greeting_patterns": greeting_counts,
        "closing_patterns": closing_counts,
        "formality": formality,
    }


def _record_style_event(user_id: str, event_type: str, details: dict) -> None:
    payload = {
        "user_id": user_id,
        "event_type": event_type,
        "details": details,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        get_admin_db().table(_STYLE_EVENT_TABLE).insert(payload).execute()
    except Exception:
        # Optional telemetry table; failures should never break user flows.
        pass


def _fetch_sent_style_samples_for_user(user_id: str, count: int = _STYLE_SAMPLE_TARGET) -> list[str]:
    svc = _gmail_service_for_user(user_id)
    result = svc.users().messages().list(
        userId="me", q="in:sent -in:chats", maxResults=count * 4
    ).execute()
    messages = result.get("messages", [])

    samples: list[str] = []
    for msg in messages:
        if len(samples) >= count:
            break
        detail = svc.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        body = _strip_quoted(_extract_plain_text(detail.get("payload", {})))
        if len(body.strip()) < 40:
            continue
        samples.append(body[:900])
    return samples


def _load_style_profile_from_db(user_id: str) -> dict | None:
    try:
        res = (
            get_admin_db()
            .table(_STYLE_PROFILE_TABLE)
            .select("user_id, style_features, sample_count, samples_preview, last_refreshed_at, next_refresh_at")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return res.data if res and res.data else None
    except Exception:
        return None


def _save_style_profile(user_id: str, samples: list[str], style_features: dict) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "sample_count": len(samples),
        "samples_preview": "\n\n---\n\n".join(samples[:_STYLE_SAMPLE_TARGET]),
        "style_features": style_features,
        "last_refreshed_at": now.isoformat(),
        "next_refresh_at": (now + timedelta(days=7)).isoformat(),
        "updated_at": now.isoformat(),
    }
    try:
        get_admin_db().table(_STYLE_PROFILE_TABLE).upsert(payload, on_conflict="user_id").execute()
    except Exception:
        pass
    return payload


def _build_and_store_style_profile_for_user(user_id: str, count: int = _STYLE_SAMPLE_TARGET) -> dict:
    samples = _fetch_sent_style_samples_for_user(user_id, count)
    style_features = _build_style_features(samples)
    profile = _save_style_profile(user_id, samples, style_features)
    _style_profile_cache[user_id] = profile
    _record_style_event(
        user_id,
        "style_profile_refreshed",
        {"sample_count": len(samples), "formality": style_features.get("formality", "balanced")},
    )
    return profile


def _ensure_style_profile(user_id: str, allow_live_refresh: bool) -> tuple[dict | None, str]:
    cached = _style_profile_cache.get(user_id)
    if cached and not _style_is_stale(cached.get("last_refreshed_at")):
        _record_style_event(user_id, "style_profile_cache_hit", {"source": "memory"})
        return cached, "memory_cache"

    db_profile = _load_style_profile_from_db(user_id)
    if db_profile and not _style_is_stale(db_profile.get("last_refreshed_at")):
        _style_profile_cache[user_id] = db_profile
        _record_style_event(user_id, "style_profile_cache_hit", {"source": "supabase"})
        return db_profile, "supabase"

    _record_style_event(user_id, "style_profile_cache_miss", {"allow_live_refresh": allow_live_refresh})
    if not allow_live_refresh:
        return db_profile, "stale_or_missing"

    try:
        refreshed = _build_and_store_style_profile_for_user(user_id, _STYLE_SAMPLE_TARGET)
        return refreshed, "live_refresh"
    except Exception as exc:
        _record_style_event(user_id, "style_profile_refresh_failed", {"error": str(exc)})
        return db_profile, "refresh_failed"


def _render_style_guidance(style_features: dict) -> str:
    if not style_features:
        return ""
    greetings = ", ".join(style_features.get("greeting_patterns", [])[:2]) or "none"
    closings = ", ".join(style_features.get("closing_patterns", [])[:2]) or "none"
    return (
        f"Formality: {style_features.get('formality', 'balanced')}\n"
        f"Avg words/sentence: {style_features.get('avg_words_per_sentence', 0)}\n"
        f"Common greetings: {greetings}\n"
        f"Common closings: {closings}"
    )


def _make_message(to: str, subject: str, body: str) -> dict:
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def _load_last_checkin() -> datetime | None:
    try:
        data = json.loads(_CHECKIN_FILE.read_text())
        return datetime.fromisoformat(data["ts"]).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _save_last_checkin(ts: datetime) -> None:
    try:
        _CHECKIN_FILE.write_text(json.dumps({"ts": ts.isoformat()}))
    except Exception:
        pass


def _relative_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


# Single-user app — store last email fetch so surface_cards can look up by index
_last_email_fetch: list = []
_style_profile_cache: dict[str, dict] = {}


async def gmail_get_updates(inp: dict) -> dict:
    """
    Fetch recent important emails from the inbox.
    Uses last check-in timestamp if since_last_checkin=True, otherwise looks back since_hours.
    Updates the check-in timestamp after every call.
    """
    since_last = inp.get("since_last_checkin", False)
    since_hours = int(inp.get("since_hours", 24))

    now = datetime.now(timezone.utc)

    if since_last:
        last = _load_last_checkin()
        after_dt = last if last else (now - timedelta(hours=24))
    else:
        after_dt = now - timedelta(hours=since_hours)

    # Gmail epoch seconds for the after: filter
    after_epoch = int(after_dt.timestamp())

    def _fetch():
        svc = _gmail_service()
        query = f"in:inbox after:{after_epoch}"
        result = svc.users().messages().list(
            userId="me", q=query, maxResults=40
        ).execute()
        messages = result.get("messages", [])

        emails = []
        for msg in messages:
            detail = svc.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            from_raw = headers.get("From", "")
            subject = headers.get("Subject", "(no subject)")
            date_raw = headers.get("Date", "")
            labels = detail.get("labelIds", [])

            # Skip automated senders
            if _NOISE_PATTERNS.search(from_raw):
                continue

            display_name, addr = email.utils.parseaddr(from_raw)
            sender = display_name or addr

            # Parse received time
            try:
                received = email.utils.parsedate_to_datetime(date_raw)
                if received.tzinfo is None:
                    received = received.replace(tzinfo=timezone.utc)
                time_str = _relative_time(received)
            except Exception:
                time_str = ""

            is_unread = "UNREAD" in labels
            is_important = "IMPORTANT" in labels

            emails.append({
                "sender": sender,
                "subject": subject,
                "snippet": detail.get("snippet", "")[:200],
                "received": time_str,
                "unread": is_unread,
                "important": is_important,
            })

        # Sort: unread + important first, then unread, then rest
        emails.sort(key=lambda e: (not e["important"], not e["unread"]))
        return emails[:15]

    global _last_email_fetch
    emails = await asyncio.to_thread(_fetch)
    _last_email_fetch = emails  # stored for surface_cards lookup by index

    # Update check-in timestamp
    await asyncio.to_thread(_save_last_checkin, now)

    if not emails:
        window = "since last check-in" if since_last else f"in the last {since_hours}h"
        return {
            "summary": f"No new emails from real people {window}.",
            "emails": [],
            "checked_at": now.isoformat(),
        }

    # Include indices so the agent can reference specific emails when calling surface_cards
    lines = [
        f"[{idx}] {e['sender']}: {e['subject']!r} ({e['received']})"
        + (" [unread]" if e["unread"] else "")
        + (f" — {e['snippet']}" if e["snippet"] else "")
        for idx, e in enumerate(emails)
    ]
    window = "since last check-in" if since_last else f"in the last {since_hours}h"
    return {
        "summary": f"{len(emails)} email(s) {window}",
        "emails": lines,
        "checked_at": now.isoformat(),
    }


async def surface_cards(inp: dict) -> dict:
    """No-op tool that tells the frontend which emails to render as cards.
    The agent calls this with the indices of emails it is about to discuss."""
    indices = inp.get("indices", [])
    selected = [_last_email_fetch[i] for i in indices if i < len(_last_email_fetch)]
    return {
        "summary": f"Showing {len(selected)} card(s)",
        "card_type": "emails",
        "items_data": selected,
    }


def _extract_plain_text(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_plain_text(part)
            if text:
                return text
    return ""


def _strip_quoted(text: str) -> str:
    """Remove quoted reply blocks (lines starting with >) and signature separators."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or stripped == "--":
            break
        lines.append(line)
    return "\n".join(lines).strip()


async def gmail_find_contact(inp: dict) -> dict:
    """Search sent history to resolve a name/company to an email address."""
    name = inp["name"]
    company = inp.get("company", "")

    def _search():
        svc = _gmail_service()
        query = f'in:sent "{name}"'
        if company:
            query += f' "{company}"'

        result = svc.users().messages().list(userId="me", q=query, maxResults=25).execute()
        messages = result.get("messages", [])

        seen: dict[str, dict] = {}  # email_lower → {email, name, count, last_subject}
        for msg in messages[:15]:   # cap API calls
            detail = svc.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["To", "Subject"],
            ).execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            to_raw = headers.get("To", "")
            subject = headers.get("Subject", "")

            for display, addr in email.utils.getaddresses([to_raw]):
                if not addr or "@" not in addr:
                    continue
                key = addr.lower()
                if key not in seen:
                    seen[key] = {"email": addr, "name": display or addr, "count": 0, "last_subject": subject}
                seen[key]["count"] += 1

        return sorted(seen.values(), key=lambda x: x["count"], reverse=True)

    contacts = await asyncio.to_thread(_search)

    if not contacts:
        return {
            "summary": f"No past emails found to anyone matching '{name}'" + (f" at '{company}'" if company else ""),
            "contacts": [],
            "best_match": None,
        }

    formatted = [
        f"{c['name']} <{c['email']}> — {c['count']} thread(s), last: {c['last_subject']!r}"
        for c in contacts[:5]
    ]
    return {
        "summary": f"Found {len(contacts)} contact(s) matching '{name}'",
        "contacts": formatted,
        "best_match": contacts[0]["email"],
        "best_match_name": contacts[0]["name"],
    }


async def gmail_fetch_style_samples(inp: dict) -> dict:
    """Return the user's style profile (weekly cached in Supabase, with live fallback)."""
    count = min(int(inp.get("count", _STYLE_SAMPLE_TARGET)), 15)
    user_id = context.current_user_id.get("")
    if not user_id:
        context.current_style_ready.set(False)
        context.current_style_profile.set({})
        return {"summary": "No authenticated user context for style profile", "samples": "", "error": True}

    profile, source = await asyncio.to_thread(_ensure_style_profile, user_id, True)
    if profile is None:
        context.current_style_ready.set(False)
        context.current_style_profile.set({})
        return {"summary": "No sent emails found for style reference", "samples": "", "error": True}

    # If the cached profile is stale and count differs, refresh to honor requested sample count.
    if source in {"stale_or_missing", "refresh_failed"}:
        try:
            profile = await asyncio.to_thread(_build_and_store_style_profile_for_user, user_id, count)
            source = "live_refresh"
        except Exception:
            pass

    style_features = profile.get("style_features") or {}
    samples = profile.get("samples_preview") or ""
    context.current_style_ready.set(True)
    context.current_style_profile.set(
        {
            "source": source,
            "last_refreshed_at": profile.get("last_refreshed_at"),
            "style_features": style_features,
        }
    )
    _record_style_event(
        user_id,
        "style_profile_loaded_for_draft",
        {"source": source, "sample_count": profile.get("sample_count", 0)},
    )
    return {
        "summary": f"Loaded style profile from {source}",
        "samples": samples,
        "style_features": style_features,
        "style_guidance": _render_style_guidance(style_features),
    }


async def gmail_draft(inp: dict) -> dict:
    to = inp["to"]
    subject = inp["subject"]
    body = inp["body"]
    user_id = context.current_user_id.get("")
    draft_preferences = context.current_draft_preferences.get({})

    if not context.current_style_ready.get(False):
        if user_id:
            profile, source = await asyncio.to_thread(_ensure_style_profile, user_id, False)
            if profile and source in {"memory_cache", "supabase"}:
                context.current_style_ready.set(True)
                context.current_style_profile.set(
                    {
                        "source": source,
                        "last_refreshed_at": profile.get("last_refreshed_at"),
                        "style_features": profile.get("style_features", {}),
                    }
                )
        if not context.current_style_ready.get(False):
            if user_id:
                _record_style_event(user_id, "draft_blocked_missing_style_profile", {"to": to})
            return {
                "summary": "Style profile unavailable. Call gmail_fetch_style_samples first.",
                "error": True,
                "requires": "gmail_fetch_style_samples",
            }

    def _create():
        svc = _gmail_service()
        draft = svc.users().drafts().create(
            userId="me",
            body={"message": _make_message(to, subject, body)},
        ).execute()
        return draft["id"]

    draft_id = await asyncio.to_thread(_create)
    if user_id:
        _record_style_event(
            user_id,
            "draft_created",
            {
                "subject_length": len(subject),
                "body_length": len(body),
                "has_preferences": bool(draft_preferences),
                "preferences": draft_preferences,
            },
        )
    return {
        "summary": f"Draft saved (to: {to}, subject: {subject!r})",
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "applied_preferences": draft_preferences,
    }


async def refresh_style_profiles_weekly(inp: dict) -> dict:
    """Cron entrypoint: refresh style profiles for users with connected Google accounts."""
    max_users = max(1, int(inp.get("max_users", 200)))

    def _refresh():
        users_res = (
            get_admin_db()
            .table("users")
            .select("id")
            .not_.is_("google_access_token", "null")
            .limit(max_users)
            .execute()
        )
        users = users_res.data or []
        refreshed = 0
        failed = 0
        errors: list[str] = []
        for row in users:
            user_id = row.get("id")
            if not user_id:
                continue
            try:
                _build_and_store_style_profile_for_user(user_id, _STYLE_SAMPLE_TARGET)
                refreshed += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{user_id}: {exc}")
        return len(users), refreshed, failed, errors[:5]

    total, refreshed, failed, errors = await asyncio.to_thread(_refresh)
    return {
        "summary": f"Weekly style refresh complete. total={total}, refreshed={refreshed}, failed={failed}",
        "total_users": total,
        "refreshed": refreshed,
        "failed": failed,
        "errors": errors,
    }


async def gmail_send(inp: dict) -> dict:
    to = inp["to"]
    subject = inp["subject"]
    body = inp["body"]

    def _send():
        svc = _gmail_service()
        sent = svc.users().messages().send(
            userId="me",
            body=_make_message(to, subject, body),
        ).execute()
        return sent["id"]

    msg_id = await asyncio.to_thread(_send)
    return {
        "summary": f"Email sent to {to} (subject: {subject!r})",
        "message_id": msg_id,
    }


async def calendar_list(inp: dict) -> dict:
    days_ahead = int(inp.get("days_ahead", 7))
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    def _list():
        svc = _calendar_service()
        result = svc.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            maxResults=15,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return result.get("items", [])

    events = await asyncio.to_thread(_list)
    formatted = []
    for ev in events:
        start = ev["start"].get("dateTime", ev["start"].get("date", ""))
        formatted.append(f"{start[:16]} — {ev.get('summary', '(no title)')}")

    return {
        "summary": f"{len(events)} event(s) in the next {days_ahead} days",
        "events": formatted,
    }


async def calendar_create(inp: dict) -> dict:
    title = inp["title"]
    start_time = inp["start_time"]
    end_time = inp["end_time"]
    description = inp.get("description", "")
    attendees = inp.get("attendees", [])

    event_body: dict = {
        "summary": title,
        "start": {"dateTime": start_time, "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": end_time, "timeZone": "America/Los_Angeles"},
    }
    if description:
        event_body["description"] = description
    if attendees:
        event_body["attendees"] = [{"email": a} for a in attendees]

    def _create():
        svc = _calendar_service()
        ev = svc.events().insert(calendarId="primary", body=event_body).execute()
        return ev.get("htmlLink", "")

    link = await asyncio.to_thread(_create)
    return {
        "summary": f"Created event '{title}' at {start_time[:16]}",
        "link": link,
    }

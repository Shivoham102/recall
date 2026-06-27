import asyncio
import base64
import email.utils
import html
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import pathlib
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from googleapiclient.discovery import build
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from google_auth import get_credentials, get_credentials_for_user
import context
from db import get_admin_db
from security.crypto import decrypt_from_storage, encrypt_for_storage

_STYLE_PROFILE_TABLE = "email_style_profiles"
_STYLE_EVENT_TABLE = "email_style_events"
_STYLE_SAMPLE_TARGET = 10
_STYLE_STALE_DAYS = 8

_GREETING_RE = re.compile(r"^\s*(hi|hello|hey|dear)\b[^\n]{0,100}", re.IGNORECASE)
_FORMAL_WORDS = {"regards", "sincerely", "appreciate", "pleased", "kindly", "thank you"}
_CASUAL_WORDS = {"hey", "thanks", "quick", "awesome", "yep", "no worries"}
_CLOSING_CANDIDATES = ("thank you", "thanks", "best", "regards", "cheers", "sincerely")
_STOP_WORDS = {
    "a", "an", "the", "to", "for", "and", "or", "but", "with", "on", "in", "at", "of", "by",
    "is", "it", "this", "that", "these", "those", "be", "am", "are", "was", "were", "as",
    "about", "follow", "followup", "follow-up", "email", "reply", "thread", "write", "send",
}

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
        for token in _CLOSING_CANDIDATES:  # longer tokens first ("thank you" before "thanks")
            if token in lowered:
                return token.capitalize()
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
            greetings.append(greeting_match.group(1).capitalize())  # keyword only, not "Hi Richard,"

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
        if not res or not res.data:
            return None
        row = res.data
        row["samples_preview"] = decrypt_from_storage(row.get("samples_preview")) or ""
        return row
    except Exception:
        return None


def _save_style_profile(user_id: str, samples: list[str], style_features: dict) -> dict:
    now = datetime.now(timezone.utc)
    samples_preview = "\n\n---\n\n".join(samples[:_STYLE_SAMPLE_TARGET])
    db_payload = {
        "user_id": user_id,
        "sample_count": len(samples),
        "samples_preview": encrypt_for_storage(samples_preview),
        "style_features": style_features,
        "last_refreshed_at": now.isoformat(),
        "next_refresh_at": (now + timedelta(days=7)).isoformat(),
        "updated_at": now.isoformat(),
    }
    try:
        get_admin_db().table(_STYLE_PROFILE_TABLE).upsert(db_payload, on_conflict="user_id").execute()
    except Exception:
        pass
    payload = {**db_payload, "samples_preview": samples_preview}
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


def _plain_to_html(text: str) -> str:
    escaped = html.escape(text)
    paragraphs = re.split(r"\n{2,}", escaped)
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())


def _make_message(to: str, subject: str, body: str) -> dict:
    msg = MIMEText(_plain_to_html(body), "html")
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def _make_reply_message(
    to: str,
    body: str,
    thread_id: str,
    subject: str = "",
    in_reply_to: str = "",
    references: str = "",
    cc: str = "",
) -> dict:
    msg = MIMEText(_plain_to_html(body), "html")
    msg["to"] = to
    if cc:
        msg["Cc"] = cc
    if subject:
        msg["subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw, "threadId": thread_id}


def _load_last_checkin() -> datetime | None:
    user_id = context.current_user_id.get(None)
    if not user_id:
        return None
    try:
        row = get_admin_db().table("users").select("last_checkin_at").eq("id", user_id).single().execute()
        val = row.data.get("last_checkin_at") if row.data else None
        if not val:
            return None
        return _to_utc(datetime.fromisoformat(val.replace("Z", "+00:00")))
    except Exception:
        return None


def _save_last_checkin(ts: datetime) -> None:
    user_id = context.current_user_id.get(None)
    if not user_id:
        return
    try:
        get_admin_db().table("users").update({"last_checkin_at": ts.isoformat()}).eq("id", user_id).execute()
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


# Last fetches for surface_* index lookups live in per-request ContextVars (see context.py),
# not module globals — globals leaked one user's fetch into another's request on warm instances.
_style_profile_cache: dict[str, dict] = {}  # keyed by user_id, so safe to share across requests


def _extract_keywords(query_text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9@._-]+", (query_text or "").lower())
    tokens: list[str] = []
    for token in raw_tokens:
        if len(token) < 3 or token in _STOP_WORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _safe_parse_date(date_raw: str) -> datetime | None:
    try:
        parsed = email.utils.parsedate_to_datetime(date_raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _recency_score(parsed: datetime | None) -> float:
    if not parsed:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 0)
    # decay from 2.0 to ~0 over a year
    return max(0.0, 2.0 - (age_days / 180.0))


def _confidence_band(score: float) -> str:
    if score >= 6.5:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


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
        messages = result.get("messages", [])[:25]  # bound batch fan-out

        # Fetch all message details in a SINGLE batched HTTP request instead of a
        # serial per-message loop. httplib2 isn't thread-safe, so a batch (one
        # round trip, one thread) is the safe way to parallelize the gets.
        details: dict[str, dict] = {}

        def _on_detail(request_id, response, exception):
            if exception is None and response is not None:
                details[request_id] = response

        batch = svc.new_batch_http_request(callback=_on_detail)
        for i, msg in enumerate(messages):
            batch.add(
                svc.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ),
                request_id=str(i),
            )
        if messages:
            batch.execute()

        emails = []
        for i, msg in enumerate(messages):
            detail = details.get(str(i))
            if detail is None:
                continue

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

            thread_id = detail.get("threadId", msg["id"])
            emails.append({
                "sender": sender,
                "subject": subject,
                "snippet": html.unescape(detail.get("snippet", ""))[:200],
                "received": time_str,
                "unread": is_unread,
                "important": is_important,
                "thread_id": thread_id,
                "link": f"https://mail.google.com/mail/u/0/#all/{thread_id}",
            })

        # Sort: unread + important first, then unread, then rest
        emails.sort(key=lambda e: (not e["important"], not e["unread"]))
        return emails[:15]

    emails = await asyncio.to_thread(_fetch)
    context.current_email_fetch.set(emails)  # per-request scratch for surface_cards index lookup

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
    source = inp.get("source", "updates")
    indices = inp.get("indices", [])
    source_items = (
        context.current_email_fetch.get([])
        if source != "thread_search"
        else context.current_thread_candidate_fetch.get([])
    )
    selected = [source_items[i] for i in indices if i < len(source_items)]
    return {
        "summary": f"Showing {len(selected)} card(s)",
        "card_type": "emails",
        "source": source,
        "items_data": selected,
    }


async def surface_calendar(inp: dict) -> dict:
    """Tells the frontend which calendar events to render as cards.
    Call after calendar_list with the indices of events worth highlighting."""
    indices = inp.get("indices", [])
    cal = context.current_calendar_fetch.get([])
    selected = [cal[i] for i in indices if i < len(cal)]
    return {
        "summary": f"Showing {len(selected)} event(s)",
        "card_type": "calendar",
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


def _role_body(msg: dict, per_msg: int = 700) -> str:
    """One message → 'USER: body' / 'COUNTERPARTY: body' line.

    Role from labelIds (SENT → USER). Quote-stripped full body, whitespace-collapsed,
    capped at per_msg chars. Always emits the role line — even when the body strips to
    empty (a pure-quoted reply) — so turn structure survives for the judge.
    """
    role = "USER" if "SENT" in msg.get("labelIds", []) else "COUNTERPARTY"
    body = _strip_quoted(_extract_plain_text(msg.get("payload", {})))
    body = re.sub(r"\s+", " ", body).strip()[:per_msg]
    return f"{role}: {body}" if body else f"{role}: (no new text)"


def thread_role_bodies_from_messages(msgs: list[dict], max_msgs: int = 6, per_msg: int = 700) -> str:
    """Role-labeled, quote-stripped bodies of the last max_msgs messages (oldest→newest)."""
    ordered = sorted(msgs, key=lambda m: int(m.get("internalDate", "0")))
    return "\n".join(_role_body(m, per_msg) for m in ordered[-max_msgs:])


def gmail_thread_role_bodies(svc, thread_id: str, max_msgs: int = 6, per_msg: int = 700) -> str:
    """Fetch a thread (format=full) and return its last messages as role-labeled bodies.

    Returns "" on fetch error so callers can degrade to whatever context they already have.
    """
    try:
        thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        return thread_role_bodies_from_messages(thread.get("messages", []), max_msgs, per_msg)
    except Exception as exc:
        print(f"[gmail_thread_role_bodies] error {thread_id[:8]}: {exc}")
        return ""


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


async def gmail_find_followup_thread(inp: dict) -> dict:
    """Find relevant follow-up thread using sent-first keyword search with inbox fallback."""
    query_text = inp.get("query_text", "").strip()
    recipient_hint = inp.get("recipient_hint", "").strip()
    lookback_days = max(7, int(inp.get("lookback_days", 365)))
    keywords = _extract_keywords(query_text)

    after_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    after_epoch = int(after_dt.timestamp())

    def _search() -> list[dict]:
        svc = _gmail_service()
        candidates: dict[str, dict] = {}
        for mailbox in ("sent", "inbox"):
            query_parts = [f"in:{mailbox}", f"after:{after_epoch}"]
            if recipient_hint:
                query_parts.append(f'"{recipient_hint}"')
            for token in keywords:
                query_parts.append(f'"{token}"')
            query = " ".join(query_parts)
            result = svc.users().messages().list(userId="me", q=query, maxResults=30).execute()
            messages = result.get("messages", [])

            for msg in messages:
                detail = svc.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date", "Message-ID", "In-Reply-To", "References"],
                ).execute()
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                thread_id = detail.get("threadId", "")
                if not thread_id:
                    continue
                from_raw = headers.get("From", "")
                to_raw = headers.get("To", "")
                subject = headers.get("Subject", "(no subject)")
                snippet = html.unescape(detail.get("snippet", ""))[:240]
                date_raw = headers.get("Date", "")

                display_from, addr_from = email.utils.parseaddr(from_raw)
                display_to, addr_to = email.utils.parseaddr(to_raw)
                counterparty = display_to or addr_to if mailbox == "sent" else display_from or addr_from
                haystack = f"{subject} {snippet} {counterparty}".lower()
                matched_terms = [t for t in keywords if t in haystack]
                overlap_score = min(len(matched_terms), 4) * 1.6
                recipient_score = 0.0
                if recipient_hint:
                    hint = recipient_hint.lower()
                    if hint in (counterparty or "").lower():
                        recipient_score = 2.0
                    elif hint in haystack:
                        recipient_score = 1.2
                recency = _recency_score(_safe_parse_date(date_raw))
                source_bonus = 0.8 if mailbox == "sent" else 0.2
                score = overlap_score + recipient_score + recency + source_bonus

                candidate = {
                    "thread_id": thread_id,
                    "message_id": msg.get("id", ""),
                    "subject": subject,
                    "counterparty": counterparty or "(unknown)",
                    "snippet": snippet,
                    "source_mailbox": mailbox,
                    "matched_terms": matched_terms,
                    "score": round(score, 2),
                    "date_raw": date_raw,
                    "received": _relative_time(_safe_parse_date(date_raw)) if date_raw else "",
                    "in_reply_to": headers.get("In-Reply-To", ""),
                    "references": headers.get("References", ""),
                }

                prev = candidates.get(thread_id)
                if prev is None or candidate["score"] > prev["score"]:
                    candidates[thread_id] = candidate
        return sorted(candidates.values(), key=lambda c: c["score"], reverse=True)

    ranked = await asyncio.to_thread(_search)

    context.current_thread_candidate_fetch.set([
        {
            "sender": c["counterparty"],
            "subject": c["subject"],
            "snippet": c["snippet"],
            "received": c["received"],
            "unread": False,
            "important": c["source_mailbox"] == "sent",
        }
        for c in ranked[:5]
    ])

    if not ranked:
        return {
            "summary": "No matching follow-up thread found. Ask a clarifying question.",
            "best_match": None,
            "confidence": "low",
            "candidates": [],
            "requires_clarification": True,
        }

    best = ranked[0]
    confidence = _confidence_band(best["score"])
    candidates = [
        (
            f"[{idx}] {c['counterparty']}: {c['subject']!r} ({c['received']}) "
            f"[{c['source_mailbox']}] terms={','.join(c['matched_terms']) or 'none'} score={c['score']}"
        )
        for idx, c in enumerate(ranked[:5])
    ]
    requires_clarification = confidence == "low"
    return {
        "summary": f"Found follow-up thread candidates (confidence: {confidence})",
        "confidence": confidence,
        "requires_clarification": requires_clarification,
        "best_match": {
            "thread_id": best["thread_id"],
            "message_id": best["message_id"],
            "subject": best["subject"],
            "counterparty": best["counterparty"],
            "source_mailbox": best["source_mailbox"],
            "reason": {
                "matched_terms": best["matched_terms"],
                "score": best["score"],
                "timestamp": best["date_raw"],
            },
            "in_reply_to": best["in_reply_to"],
            "references": best["references"],
        },
        "candidates": candidates,
        "candidate_count": len(ranked[:5]),
        "suggested_surface_indices": list(range(min(len(ranked), 3))),
    }


async def gmail_get_thread_context(inp: dict) -> dict:
    """Fetch recent messages in a thread and summarize actionable context."""
    thread_id = inp["thread_id"]
    max_messages = max(2, min(int(inp.get("max_messages", 6)), 12))

    def _fetch():
        svc = _gmail_service()
        thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        messages = thread.get("messages", [])
        # Keep most recent messages to preserve short context window.
        messages = sorted(messages, key=lambda m: int(m.get("internalDate", "0")), reverse=True)[:max_messages]

        items: list[dict] = []
        asks: list[str] = []
        commitments: list[str] = []
        action_items: list[str] = []

        for msg in messages:
            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            from_raw = headers.get("From", "")
            subject = headers.get("Subject", "(no subject)")
            date_raw = headers.get("Date", "")
            body = _strip_quoted(_extract_plain_text(payload))[:1200]
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]

            for ln in lines:
                lower_ln = ln.lower()
                if "?" in ln and len(asks) < 4:
                    asks.append(ln[:180])
                if re.search(r"\b(i('| wi)?ll|we('?| wi)?ll|can do|will do)\b", lower_ln) and len(commitments) < 4:
                    commitments.append(ln[:180])
                if re.search(r"\b(please|could you|can you|let's|next step|follow up)\b", lower_ln) and len(action_items) < 4:
                    action_items.append(ln[:180])

            items.append(
                {
                    "from": from_raw,
                    "subject": subject,
                    "date": date_raw,
                    "message_id": headers.get("Message-ID", ""),
                    "in_reply_to": headers.get("In-Reply-To", ""),
                    "references": headers.get("References", ""),
                    "body_excerpt": body[:400],
                }
            )

        recent_points = []
        if asks:
            recent_points.append(f"Asks: {' | '.join(asks[:2])}")
        if commitments:
            recent_points.append(f"Commitments: {' | '.join(commitments[:2])}")
        if action_items:
            recent_points.append(f"Action items: {' | '.join(action_items[:2])}")
        summary = " ; ".join(recent_points) if recent_points else "No explicit asks found; continue the latest thread topic."

        latest = items[0] if items else {}
        return {
            "messages": items,
            "summary": summary,
            "latest_message_id": latest.get("message_id", ""),
            "latest_in_reply_to": latest.get("in_reply_to", ""),
            "latest_references": latest.get("references", ""),
        }

    data = await asyncio.to_thread(_fetch)
    latest_id = data["latest_message_id"]
    ref_parts = data["latest_references"].split() if data["latest_references"] else []
    if latest_id and latest_id not in ref_parts:
        ref_parts.append(latest_id)
    return {
        "summary": "Loaded thread context for follow-up drafting",
        "thread_id": thread_id,
        "context_summary": data["summary"],
        "recent_messages": data["messages"],
        "latest_message_id": latest_id,
        "in_reply_to": latest_id,
        "references": " ".join(ref_parts),
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


async def gmail_reply_draft(inp: dict) -> dict:
    """Create a Gmail draft reply within an existing thread."""
    thread_id = inp["thread_id"]
    to = inp["to"]
    subject = inp.get("subject", "")
    body = inp["body"]
    in_reply_to = inp.get("in_reply_to", "")
    references = inp.get("references", "")
    cc = inp.get("cc", "")

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
                _record_style_event(user_id, "reply_draft_blocked_missing_style_profile", {"thread_id": thread_id})
            return {
                "summary": "Style profile unavailable. Call gmail_fetch_style_samples first.",
                "error": True,
                "requires": "gmail_fetch_style_samples",
            }

    def _create():
        svc = _gmail_service()
        draft = svc.users().drafts().create(
            userId="me",
            body={"message": _make_reply_message(to, body, thread_id, subject, in_reply_to, references, cc)},
        ).execute()
        return draft["id"]

    draft_id = await asyncio.to_thread(_create)
    if user_id:
        _record_style_event(
            user_id,
            "reply_draft_created",
            {
                "thread_id": thread_id,
                "subject_length": len(subject),
                "body_length": len(body),
                "has_preferences": bool(draft_preferences),
                "preferences": draft_preferences,
            },
        )

    return {
        "summary": f"Reply draft saved in thread {thread_id[:12]}...",
        "draft_id": draft_id,
        "thread_id": thread_id,
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
    days_ahead = int(inp.get("days_ahead", 2))
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
    cal_fetch: list = []
    formatted = []
    for ev in events:
        start_raw = ev["start"].get("dateTime", ev["start"].get("date", ""))
        end_raw   = ev["end"].get("dateTime",   ev["end"].get("date",   ""))
        is_all_day = "dateTime" not in ev["start"]
        cal_fetch.append({
            "title":      ev.get("summary", "(no title)"),
            "start":      start_raw,
            "end":        end_raw,
            "link":       ev.get("htmlLink", ""),
            "location":   ev.get("location", ""),
            "is_all_day": is_all_day,
        })
        formatted.append(f"[{len(cal_fetch) - 1}] {start_raw[:16]} — {ev.get('summary', '(no title)')}")

    context.current_calendar_fetch.set(cal_fetch)  # per-request scratch for surface_calendar
    return {
        "summary": f"{len(events)} event(s) in the next {days_ahead} days",
        "events": formatted,
    }


async def calendar_events_window(days_back: int = 7, days_ahead: int = 60) -> list[dict]:
    """Rich calendar fetch for follow-up correlation.

    Wider and richer than calendar_list: includes organizer/attendees/description so an LLM
    can match a thread to a scheduled meeting by company/role/people/topic. Window spans the
    recent past (a just-accepted invite) through weeks ahead (interviews land far out).
    Returns [] on any error so callers degrade to email-only judgment.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)

    def _list():
        svc = _calendar_service()
        result = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            maxResults=100,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return result.get("items", [])

    try:
        events = await asyncio.to_thread(_list)
    except Exception as exc:
        print(f"[calendar_events_window] error: {exc}")
        return []

    out: list[dict] = []
    for ev in events:
        start_raw = ev["start"].get("dateTime", ev["start"].get("date", ""))
        attendees = [
            (a.get("displayName") or a.get("email", "")).strip()
            for a in ev.get("attendees", []) if a.get("email")
        ]
        organizer = ev.get("organizer", {})
        out.append({
            "title": ev.get("summary", "(no title)"),
            "start": start_raw,
            "organizer": organizer.get("displayName") or organizer.get("email", ""),
            "attendees": [a for a in attendees if a],
            "description": (ev.get("description", "") or "")[:300],
        })
    return out


async def calendar_create(inp: dict) -> dict:
    title = inp["title"]
    start_time = inp["start_time"]
    end_time = inp["end_time"]
    description = inp.get("description", "")
    attendees = inp.get("attendees", [])

    _raw_tz = context.current_user_tz.get("UTC")
    try:
        ZoneInfo(_raw_tz)
        _event_tz = _raw_tz
    except ZoneInfoNotFoundError:
        _event_tz = "UTC"
    event_body: dict = {
        "summary": title,
        "start": {"dateTime": start_time, "timeZone": _event_tz},
        "end": {"dateTime": end_time, "timeZone": _event_tz},
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

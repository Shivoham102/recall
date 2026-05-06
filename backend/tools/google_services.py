import asyncio
import base64
import email.utils
import json
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import pathlib
import re
import sys

from googleapiclient.discovery import build
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from google_auth import get_credentials, get_credentials_for_user
import context

_CHECKIN_FILE = pathlib.Path(__file__).parent.parent / "last_checkin.json"

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
    """Fetch recent sent emails as writing style examples for the agent to imitate."""
    count = min(int(inp.get("count", 8)), 15)

    def _fetch():
        svc = _gmail_service()
        result = svc.users().messages().list(
            userId="me", q="in:sent -in:chats", maxResults=count * 3
        ).execute()
        messages = result.get("messages", [])

        samples = []
        for msg in messages:
            if len(samples) >= count:
                break
            detail = svc.users().messages().get(userId="me", id=msg["id"], format="full").execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            subject = headers.get("Subject", "(no subject)")

            body = _strip_quoted(_extract_plain_text(detail["payload"]))
            if len(body.strip()) < 40:
                continue  # skip one-liners and empty replies

            samples.append(f"Subject: {subject}\n{body[:600]}")

        return samples

    samples = await asyncio.to_thread(_fetch)

    if not samples:
        return {"summary": "No sent emails found for style reference", "samples": ""}

    return {
        "summary": f"Fetched {len(samples)} sent emails for style reference",
        "samples": "\n\n---\n\n".join(samples),
    }


async def gmail_draft(inp: dict) -> dict:
    to = inp["to"]
    subject = inp["subject"]
    body = inp["body"]

    def _create():
        svc = _gmail_service()
        draft = svc.users().drafts().create(
            userId="me",
            body={"message": _make_message(to, subject, body)},
        ).execute()
        return draft["id"]

    draft_id = await asyncio.to_thread(_create)
    return {
        "summary": f"Draft saved (to: {to}, subject: {subject!r})",
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
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

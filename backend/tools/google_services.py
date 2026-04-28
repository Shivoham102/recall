import asyncio
import base64
import email.utils
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import re

from googleapiclient.discovery import build
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from google_auth import get_credentials


def _gmail_service():
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def _calendar_service():
    return build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)


def _make_message(to: str, subject: str, body: str) -> dict:
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


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

import asyncio
import base64
import email.utils
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Annotated

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client, Client
from line.llm_agent import loopback_tool

_service_client: Client | None = None

_NOISE_PATTERNS = re.compile(
    r"(noreply|no-reply|donotreply|do-not-reply|notification|newsletter"
    r"|mailer-daemon|postmaster|bounce|alerts?@|updates?@|support@"
    r"|unsubscribe|marketing|digest|automated)",
    re.IGNORECASE,
)


def _service_db() -> Client:
    global _service_client
    if _service_client is None:
        _service_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _service_client


def _get_google_creds(user_id: str) -> Credentials:
    row = _service_db().table("users").select(
        "google_access_token, google_refresh_token, google_token_expiry"
    ).eq("id", user_id).single().execute().data
    return Credentials(
        token=row["google_access_token"],
        refresh_token=row["google_refresh_token"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )


def _gmail_service(user_id: str):
    return build("gmail", "v1", credentials=_get_google_creds(user_id), cache_discovery=False)


def _calendar_service(user_id: str):
    return build("calendar", "v3", credentials=_get_google_creds(user_id), cache_discovery=False)


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


def _extract_plain_text(payload: dict) -> str:
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
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or stripped == "--":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def make_gmail_get_updates(user_id: str):
    last_fetch: list = []

    @loopback_tool
    async def gmail_get_updates(
        ctx,
        since_last_checkin: Annotated[bool, "Fetch emails since last check-in"] = False,
        since_hours: Annotated[int, "Hours to look back, default 24"] = 24,
    ):
        """Fetch recent emails from inbox filtered to real people. Call surface_cards after."""
        now = datetime.now(timezone.utc)
        after_dt = now - timedelta(hours=since_hours)

        def _fetch():
            svc = _gmail_service(user_id)
            after_epoch = int(after_dt.timestamp())
            result = svc.users().messages().list(
                userId="me", q=f"in:inbox after:{after_epoch}", maxResults=40
            ).execute()
            emails = []
            for msg in result.get("messages", []):
                detail = svc.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
                from_raw = headers.get("From", "")
                if _NOISE_PATTERNS.search(from_raw):
                    continue
                display_name, addr = email.utils.parseaddr(from_raw)
                sender = display_name or addr
                try:
                    received = email.utils.parsedate_to_datetime(headers.get("Date", ""))
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=timezone.utc)
                    time_str = _relative_time(received)
                except Exception:
                    time_str = ""
                labels = detail.get("labelIds", [])
                emails.append({
                    "sender": sender,
                    "subject": headers.get("Subject", "(no subject)"),
                    "snippet": detail.get("snippet", "")[:200],
                    "received": time_str,
                    "unread": "UNREAD" in labels,
                    "important": "IMPORTANT" in labels,
                })
            emails.sort(key=lambda e: (not e["important"], not e["unread"]))
            return emails[:15]

        emails = await asyncio.to_thread(_fetch)
        last_fetch.clear()
        last_fetch.extend(emails)

        lines = [
            f"[{idx}] {e['sender']}: {e['subject']!r} ({e['received']})"
            + (" [unread]" if e["unread"] else "")
            + (f" — {e['snippet']}" if e["snippet"] else "")
            for idx, e in enumerate(emails)
        ]
        window = f"in the last {since_hours}h"
        return {
            "summary": f"{len(emails)} email(s) {window}",
            "emails": lines,
            "checked_at": now.isoformat(),
        }

    gmail_get_updates._last_fetch = last_fetch
    return gmail_get_updates


def make_surface_cards(user_id: str, fetch_ref: list):
    @loopback_tool
    async def surface_cards(
        ctx,
        indices: Annotated[list[int], "Indices from gmail_get_updates to display as cards"],
    ):
        """Display specific emails as visual cards in the UI. Call after gmail_get_updates."""
        selected = [fetch_ref[i] for i in indices if i < len(fetch_ref)]
        idempotency_id = str(uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{user_id}:{getattr(ctx, 'tool_call_id', str(indices))}",
        ))
        await asyncio.to_thread(
            lambda: _service_db().table("ui_events").upsert({
                "id": idempotency_id,
                "user_id": user_id,
                "type": "surface_cards",
                "payload": {"items": selected},
            }, on_conflict="id").execute()
        )
        return {"summary": f"Showing {len(selected)} card(s)", "count": len(selected)}

    return surface_cards


def make_gmail_find_contact(user_id: str):
    @loopback_tool
    async def gmail_find_contact(
        ctx,
        name: Annotated[str, "Person's name to search for"],
        company: Annotated[str, "Company or domain to narrow the search, optional"] = "",
    ):
        """Search sent email history to resolve a name to an email address."""
        def _search():
            svc = _gmail_service(user_id)
            query = f'in:sent "{name}"'
            if company:
                query += f' "{company}"'
            result = svc.users().messages().list(userId="me", q=query, maxResults=25).execute()
            seen: dict[str, dict] = {}
            for msg in result.get("messages", [])[:15]:
                detail = svc.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["To", "Subject"],
                ).execute()
                headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
                for display, addr in email.utils.getaddresses([headers.get("To", "")]):
                    if not addr or "@" not in addr:
                        continue
                    key = addr.lower()
                    if key not in seen:
                        seen[key] = {"email": addr, "name": display or addr, "count": 0,
                                     "last_subject": headers.get("Subject", "")}
                    seen[key]["count"] += 1
            return sorted(seen.values(), key=lambda x: x["count"], reverse=True)

        contacts = await asyncio.to_thread(_search)
        if not contacts:
            return {"summary": f"No contacts found matching '{name}'", "contacts": [], "best_match": None}
        formatted = [
            f"{c['name']} <{c['email']}> — {c['count']} thread(s), last: {c['last_subject']!r}"
            for c in contacts[:5]
        ]
        return {
            "summary": f"Found {len(contacts)} contact(s)",
            "contacts": formatted,
            "best_match": contacts[0]["email"],
            "best_match_name": contacts[0]["name"],
        }

    return gmail_find_contact


def make_gmail_fetch_style_samples(user_id: str):
    @loopback_tool
    async def gmail_fetch_style_samples(
        ctx,
        count: Annotated[int, "Number of emails to fetch, default 8, max 15"] = 8,
    ):
        """Fetch recent sent emails as writing style examples. Always call before gmail_draft."""
        count = min(count, 15)

        def _fetch():
            svc = _gmail_service(user_id)
            result = svc.users().messages().list(
                userId="me", q="in:sent -in:chats", maxResults=count * 3
            ).execute()
            samples = []
            for msg in result.get("messages", []):
                if len(samples) >= count:
                    break
                detail = svc.users().messages().get(userId="me", id=msg["id"], format="full").execute()
                headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
                body = _strip_quoted(_extract_plain_text(detail["payload"]))
                if len(body.strip()) < 40:
                    continue
                samples.append(f"Subject: {headers.get('Subject', '(no subject)')}\n{body[:600]}")
            return samples

        samples = await asyncio.to_thread(_fetch)
        if not samples:
            return {"summary": "No sent emails found for style reference", "samples": ""}
        return {"summary": f"Fetched {len(samples)} sent emails", "samples": "\n\n---\n\n".join(samples)}

    return gmail_fetch_style_samples


def make_gmail_draft(user_id: str):
    @loopback_tool
    async def gmail_draft(
        ctx,
        to: Annotated[str, "Recipient email address"],
        subject: Annotated[str, "Email subject"],
        body: Annotated[str, "Email body"],
    ):
        """Save a Gmail draft without sending. Call gmail_find_contact and gmail_fetch_style_samples first."""
        def _create():
            msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            svc = _gmail_service(user_id)
            draft = svc.users().drafts().create(
                userId="me", body={"message": {"raw": raw}}
            ).execute()
            return draft["id"]

        draft_id = await asyncio.to_thread(_create)
        return {"summary": f"Draft saved (to: {to}, subject: {subject!r})", "draft_id": draft_id}

    return gmail_draft


def make_calendar_list(user_id: str):
    @loopback_tool
    async def calendar_list(
        ctx,
        days_ahead: Annotated[int, "Days to look ahead, default 7"] = 7,
    ):
        """List upcoming Google Calendar events."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        def _list():
            svc = _calendar_service(user_id)
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
        formatted = [
            f"{ev['start'].get('dateTime', ev['start'].get('date', ''))[:16]} — {ev.get('summary', '(no title)')}"
            for ev in events
        ]
        return {"summary": f"{len(events)} event(s) in the next {days_ahead} days", "events": formatted}

    return calendar_list


def make_calendar_create(user_id: str):
    @loopback_tool
    async def calendar_create(
        ctx,
        title: Annotated[str, "Event title"],
        start_time: Annotated[str, "ISO 8601 datetime, e.g. 2026-05-10T15:00:00"],
        end_time: Annotated[str, "ISO 8601 datetime"],
        description: Annotated[str, "Event description, optional"] = "",
        attendees: Annotated[list[str], "List of attendee emails, optional"] = [],
    ):
        """Create a Google Calendar event. Only call after user confirmation."""
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
            svc = _calendar_service(user_id)
            ev = svc.events().insert(calendarId="primary", body=event_body).execute()
            return ev.get("htmlLink", "")

        link = await asyncio.to_thread(_create)
        return {"summary": f"Created event '{title}' at {start_time[:16]}", "link": link}

    return calendar_create

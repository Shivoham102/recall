"""
Follow-up scan — scripted pipeline (runs hourly).

Searches sent mail for commitment language, tracks open follow-ups in
follow_up_threads, and nudges the user for pending ones.
"""
import asyncio
import email.utils
import html
import re
from datetime import datetime, timedelta, timezone

from db import get_admin_db
from proactive.memory_context import get_proactive_memory_context
from proactive.runner import ProactiveResult

_CONCLUDED_PATTERNS = re.compile(
    r"\b(no worries|no problem|sounds good|all (set|good)|got it|noted|"
    r"understood|will do|take care|talk soon|stay in touch|"
    r"thanks for|thank you for|appreciate it|works for me|"
    r"let'?s catch up|have a good|no need to|don'?t worry)\b",
    re.IGNORECASE,
)

_COMMITMENT_QUERY = (
    '"follow up" OR "get back to you" OR "I\'ll check" OR "will send"'
    ' OR "check back" OR "following up" OR "circle back"'
)
_LOOKBACK_DAYS = 7
_NUDGE_INTERVAL_HOURS = 48  # only re-nudge an existing item after 48h


def _get_gmail_service():
    from tools.google_services import _gmail_service  # noqa: PLC0415
    return _gmail_service()


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + "..."


def _memory_score(item: dict, memory_context: str) -> int:
    if not memory_context:
        return 0
    haystack = " ".join(str(item.get(k, "")) for k in ("counterparty", "subject", "commitment_text")).lower()
    score = 0
    for token in set(memory_context.lower().replace("-", " ").split()):
        if len(token) >= 5 and token in haystack:
            score += 1
    return score


def _thread_is_concluded(svc, thread_id: str) -> bool:
    """Return True if the thread's most recent message suggests conversation concluded."""
    try:
        thread = svc.users().threads().get(
            userId="me", id=thread_id, format="minimal"
        ).execute()
        messages = sorted(
            thread.get("messages", []),
            key=lambda m: int(m.get("internalDate", "0")),
            reverse=True,
        )
        if not messages:
            return False
        latest = messages[0]
        # Latest message is from user (SENT) — no reply yet, still open
        if "SENT" in latest.get("labelIds", []):
            return False
        # Someone replied — check if snippet signals a closed conversation
        snippet = html.unescape(latest.get("snippet", ""))
        return bool(_CONCLUDED_PATTERNS.search(snippet))
    except Exception:
        return False


def _search_commitments() -> list[dict]:
    svc = _get_gmail_service()
    after_epoch = int(
        (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).timestamp()
    )
    query = f"in:sent after:{after_epoch} ({_COMMITMENT_QUERY})"
    result = svc.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = result.get("messages", [])

    seen_threads: set[str] = set()
    threads: list[dict] = []

    for msg in messages:
        detail = svc.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["To", "Subject", "Date"],
        ).execute()

        thread_id = detail.get("threadId", "")
        if not thread_id or thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)

        headers = {
            h["name"]: h["value"]
            for h in detail.get("payload", {}).get("headers", [])
        }
        to_raw = headers.get("To", "")
        subject = headers.get("Subject", "(no subject)")
        snippet = detail.get("snippet", "")[:200]

        display_to, addr_to = email.utils.parseaddr(to_raw)
        counterparty = display_to or addr_to or "(unknown)"

        threads.append({
            "thread_id": thread_id,
            "counterparty": counterparty,
            "subject": subject,
            "commitment_text": html.unescape(snippet),
        })

    return [t for t in threads if not _thread_is_concluded(svc, t["thread_id"])]


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    db = get_admin_db()
    now = datetime.now(timezone.utc)
    memory_context = await get_proactive_memory_context(user_id, "follow up relationships projects commitments")

    # Find commitment threads in sent mail
    commitments = await asyncio.to_thread(_search_commitments)

    # Load existing open follow-up threads for this user
    existing_res = (
        db.table("follow_up_threads")
        .select("id, thread_id, counterparty, commitment_text, nudge_count, last_nudged_at, status")
        .eq("user_id", user_id)
        .eq("status", "open")
        .execute()
    )
    existing_by_thread: dict[str, dict] = {
        row["thread_id"]: row for row in (existing_res.data or [])
    }

    new_items: list[dict] = []
    nudge_due: list[dict] = []
    nudge_interval = timedelta(hours=_NUDGE_INTERVAL_HOURS)

    for commitment in commitments:
        thread_id = commitment["thread_id"]
        if thread_id in existing_by_thread:
            row = existing_by_thread[thread_id]
            # Only re-nudge if interval has elapsed
            last_nudged = row.get("last_nudged_at")
            if last_nudged:
                try:
                    ln_dt = datetime.fromisoformat(last_nudged.replace("Z", "+00:00"))
                    if (now - ln_dt.astimezone(timezone.utc)) < nudge_interval:
                        continue  # too soon to nudge again
                except Exception:
                    pass
            db.table("follow_up_threads").update({
                "nudge_count": row["nudge_count"] + 1,
                "last_nudged_at": now.isoformat(),
            }).eq("id", row["id"]).execute()
            nudge_due.append(row)
        else:
            # Insert new follow-up thread
            insert_res = (
                db.table("follow_up_threads")
                .insert({
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "counterparty": commitment["counterparty"],
                    "commitment_text": commitment["commitment_text"][:300],
                    "source": "email",
                    "detected_at": now.isoformat(),
                })
                .execute()
            )
            if insert_res.data:
                new_items.append({**commitment, "id": insert_res.data[0]["id"]})

    all_pending = new_items + nudge_due
    if memory_context:
        all_pending = sorted(all_pending, key=lambda item: _memory_score(item, memory_context), reverse=True)
    if not all_pending:
        return ProactiveResult(
            text="Follow-up scan complete — nothing pending",
            job_type="follow_up_scan",
            deliver=False,
        )

    task_cards = [
        {
            "id": item.get("id", item.get("thread_id", "")),
            "content": (
                f"Follow up with {item.get('counterparty', '?')}: "
                + _truncate(item.get("commitment_text") or item.get("subject", ""), 100)
            ),
            "intent_type": "follow_up",
            "status": "open",
            "created_at": now.isoformat(),
            "due_hint": None,
            "link": f"https://mail.google.com/mail/u/0/#all/{item.get('thread_id', '')}" if item.get("thread_id") else None,
        }
        for item in all_pending
    ]

    count = len(all_pending)
    new_count = len(new_items)
    text = f"Follow-up scan — {count} pending follow-up{'s' if count != 1 else ''}"
    if new_count:
        text += f" ({new_count} new)"

    return ProactiveResult(
        text=text,
        job_type="follow_up_scan",
        task_cards=task_cards,
        metadata={"memory_context_used": bool(memory_context)},
    )

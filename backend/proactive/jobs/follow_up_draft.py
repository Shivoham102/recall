"""
Follow-up draft — runs daily at 6am via Vercel cron (before morning brief).

Responsibilities per user:
1. Backfill last_user_sent_at for existing open threads that have NULL.
2. Verify existing Gmail drafts — if gone, determine sent (→ resolve) or deleted (→ reset silently).
3. Refresh email style profile if stale > 8 days (absorbs refresh-email-style-profiles cron).
4. Auto-draft follow-up emails for open threads where last_user_sent_at >= 48h ago and no draft yet.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

from db import get_admin_db
from proactive.memory_context import get_proactive_memory_context
from proactive.runner import ProactiveResult

_DRAFT_NOISE = re.compile(
    r"(noreply|no-reply|donotreply|careers@|@email\.|@em\.|@emails\."
    r"|application received|thank you for apply|automatically generated"
    r"|newsletter|unsubscribe|marketing|promotional)",
    re.IGNORECASE,
)


def _t(label: str, t0: float) -> float:
    elapsed = time.perf_counter() - t0
    print(f"[follow_up_draft] {label}: {elapsed:.1f}s")
    return time.perf_counter()


def _get_gmail_service():
    from tools.google_services import _gmail_service  # noqa: PLC0415
    return _gmail_service()


def _parse_ts(iso_str: str) -> float:
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp() if iso_str else 0.0
    except Exception:
        return 0.0


def _get_thread_draft_meta(svc, thread_id: str) -> dict:
    """
    Two-call fetch per draft thread:
      1. format=minimal  → message snippets + labelIds (for context summary)
      2. messages.get(format=metadata) on last message → Message-ID, References, To, Cc
    If last message is not SENT (counterparty replied last), does a 3rd call for To/Cc
    from the last SENT message. Typical follow-up case (user sent last) = 2 calls.
    Returns in_reply_to, references, cc, to, context_summary.
    """
    import email.utils as _eu  # noqa: PLC0415
    import html as _html  # noqa: PLC0415
    try:
        # Call 1: minimal — snippets + labelIds for all messages
        thread = svc.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
        msgs = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", "0")))
        if not msgs:
            return {"in_reply_to": "", "references": "", "cc": "", "to": "", "context_summary": ""}

        lines = []
        for msg in msgs[-6:]:
            role = "USER" if "SENT" in msg.get("labelIds", []) else "COUNTERPARTY"
            snippet = _html.unescape(msg.get("snippet", ""))[:300]
            if snippet:
                lines.append(f"{role}: {snippet}")
        context_summary = "\n".join(lines)

        last_msg = msgs[-1]
        last_sent = next((m for m in reversed(msgs) if "SENT" in m.get("labelIds", [])), None)

        # Call 2: metadata on last message for threading headers + To/Cc (if SENT)
        detail = svc.users().messages().get(
            userId="me", id=last_msg["id"], format="metadata",
            metadataHeaders=["Message-ID", "References", "To", "Cc"],
        ).execute()
        hdrs = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        latest_id = hdrs.get("Message-ID", "")
        ref_parts = hdrs.get("References", "").split() if hdrs.get("References") else []
        if latest_id and latest_id not in ref_parts:
            ref_parts.append(latest_id)

        # Use To/Cc from detail if last msg is SENT; otherwise fetch last sent message
        if "SENT" not in last_msg.get("labelIds", []) and last_sent:
            sent_detail = svc.users().messages().get(
                userId="me", id=last_sent["id"], format="metadata",
                metadataHeaders=["To", "Cc"],
            ).execute()
            sent_hdrs = {h["name"]: h["value"] for h in sent_detail.get("payload", {}).get("headers", [])}
        else:
            sent_hdrs = hdrs

        cc_pairs = _eu.getaddresses([sent_hdrs.get("Cc", "")]) if sent_hdrs.get("Cc") else []
        cc_str = ", ".join(addr for _, addr in cc_pairs if addr)
        to_raw = sent_hdrs.get("To", "")
        display_to, addr_to = _eu.parseaddr(to_raw)
        to_str = (
            f"{display_to} <{addr_to}>" if display_to and addr_to
            else addr_to or display_to or ""
        )

        return {
            "in_reply_to": latest_id,
            "references": " ".join(ref_parts),
            "cc": cc_str,
            "to": to_str,
            "context_summary": context_summary,
        }
    except Exception as exc:
        print(f"[follow_up_draft] _get_thread_draft_meta error {thread_id[:8]}: {exc}")
    return {"in_reply_to": "", "references": "", "cc": "", "to": "", "context_summary": ""}


def _get_display_name(svc) -> str:
    """Fetch primary sendAs display name to use as email sign-off."""
    try:
        aliases = svc.users().settings().sendAs().list(userId="me").execute()
        for alias in aliases.get("sendAs", []):
            if alias.get("isPrimary"):
                return alias.get("displayName", "")
        send_as_list = aliases.get("sendAs", [])
        if send_as_list:
            return send_as_list[0].get("displayName", "")
    except Exception as exc:
        print(f"[follow_up_draft] _get_display_name error: {exc}")
    return ""


async def _haiku_draft(
    context_summary: str,
    commitment: str,
    counterparty: str,
    memory_context: str,
    formality: str,
    avg_words: int,
    greeting: str,
    closing: str,
    display_name: str = "",
) -> str:
    from anthropic import AsyncAnthropic  # noqa: PLC0415
    import os  # noqa: PLC0415
    sign_off = f"{closing},\n{display_name}" if display_name else f"{closing},"
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content":
            f"Write a follow-up email body to {counterparty}.\n"
            f"Thread context: {context_summary}\n"
            f"Original commitment/request: {commitment}\n"
            f"User context: {memory_context or 'none'}\n"
            f"Style: {formality} tone, ~{avg_words} words/sentence, "
            f"open with '{greeting}'\n"
            f"Rules: 2-4 sentences, no em dashes, do not identify as AI, "
            f"reference the specific commitment or ask from the thread. "
            f"End with exactly: {sign_off}"}],
    )
    return resp.content[0].text.strip()


async def _run_draft_phase(
    user_id: str,
    db,
    svc,
    now: datetime,
    memory_context: str,
) -> list[dict]:
    """Draft follow-up emails for qualifying open threads (called only from follow_up_draft.run())."""
    import context  # ContextVar module — context.py:7  # noqa: PLC0415
    from tools.google_services import (  # noqa: PLC0415
        gmail_fetch_style_samples,
        gmail_reply_draft,
    )

    cutoff = (now - timedelta(hours=48)).isoformat()
    candidates = (
        db.table("follow_up_threads")
        .select("id, thread_id, counterparty, commitment_text")
        .eq("user_id", user_id)
        .eq("status", "open")
        .is_("draft_gmail_id", "null")
        .not_.is_("last_user_sent_at", "null")
        .lte("last_user_sent_at", cutoff)
        .order("last_user_sent_at")
        .execute()
    ).data or []

    if not candidates:
        print(f"[follow_up_draft] _run_draft_phase: no candidates (cutoff={cutoff})")
        return []

    print(f"[follow_up_draft] _run_draft_phase: {len(candidates)} candidates to draft")
    t0 = time.perf_counter()

    # Load/refresh style profile — sets context.current_style_ready + context.current_style_profile
    # Verified: google_services.py:843-850 sets context.current_style_profile with style_features
    style_result = await gmail_fetch_style_samples({})
    t0 = _t("gmail_fetch_style_samples", t0)
    if style_result.get("error"):
        print(f"[follow_up_draft] style profile error: {style_result.get('error')}")
        return []  # no style profile — skip drafting for this user

    style_feats = context.current_style_profile.get({}).get("style_features", {})
    formality = style_feats.get("formality", "balanced")
    avg_words = int(style_feats.get("avg_words_per_sentence") or 15)
    greeting = (style_feats.get("greeting_patterns") or ["Hi"])[0]
    closing = (style_feats.get("closing_patterns") or ["Thanks"])[0]

    display_name = await asyncio.to_thread(_get_display_name, svc)
    print(f"[follow_up_draft] display_name={display_name!r}")

    # Pre-filter: skip noise candidates before hitting Gmail/Haiku APIs
    clean_candidates = [
        t for t in candidates
        if not (_DRAFT_NOISE.search(t.get("counterparty", "")) or
                _DRAFT_NOISE.search(t.get("commitment_text", "")))
    ]
    if len(clean_candidates) < len(candidates):
        print(f"[follow_up_draft] noise gate dropped {len(candidates) - len(clean_candidates)} candidate(s)")

    # Deduplicate by counterparty bare email — one draft per sender (most-recent wins)
    import email.utils as _eu  # noqa: PLC0415
    by_counterparty: dict[str, dict] = {}
    for t in clean_candidates:  # ordered by last_user_sent_at ASC — overwrite keeps newest
        raw_cp = t.get("counterparty", "")
        _, addr = _eu.parseaddr(raw_cp)
        cp_key = (addr or raw_cp).lower()
        by_counterparty[cp_key] = t
    clean_candidates = list(by_counterparty.values())[:5]  # max 5 drafts per run
    print(f"[follow_up_draft] {len(clean_candidates)} candidates after counterparty dedup (capped at 5)")

    async def _draft_one(thread: dict) -> dict | None:
        try:
            t_start = time.perf_counter()
            meta = await asyncio.to_thread(_get_thread_draft_meta, svc, thread["thread_id"])
            counterparty = meta["to"] or thread["counterparty"]
            in_reply_to = meta["in_reply_to"]
            references = meta["references"]
            cc_str = meta["cc"]
            context_summary = meta.get("context_summary") or thread["commitment_text"]

            draft_body = await _haiku_draft(
                context_summary=context_summary,
                commitment=thread["commitment_text"],
                counterparty=counterparty,
                memory_context=memory_context,
                formality=formality,
                avg_words=avg_words,
                greeting=greeting,
                closing=closing,
                display_name=display_name,
            )

            result = await gmail_reply_draft({
                "thread_id": thread["thread_id"],
                "to": counterparty,
                "body": draft_body,
                "subject": "",
                "in_reply_to": in_reply_to,
                "references": references,
                "cc": cc_str,
            })
            print(f"[follow_up_draft] draft_for_{thread['thread_id'][:8]}: {time.perf_counter() - t_start:.1f}s")
            if result.get("error"):
                print(f"[follow_up_draft] gmail_reply_draft error: {result.get('error')}")
                return None

            draft_id = result["draft_id"]
            try:
                db.table("follow_up_threads").update({
                    "draft_gmail_id": draft_id,
                    "draft_created_at": now.isoformat(),
                    "last_nudged_at": now.isoformat(),
                }).eq("id", thread["id"]).execute()
            except Exception as db_exc:
                print(f"[follow_up_draft] DB update failed for draft {draft_id}: {db_exc} — deleting Gmail draft")
                try:
                    await asyncio.to_thread(
                        lambda: svc.users().drafts().delete(userId="me", id=draft_id).execute()
                    )
                except Exception:
                    pass
                return None

            return {
                "id": thread["id"],
                "content": f"Draft ready: follow up with {counterparty}",
                "intent_type": "follow_up_draft",
                "status": "open",
                "created_at": now.isoformat(),
                "link": f"https://mail.google.com/mail/u/0/#all/{thread['thread_id']}",
            }
        except Exception as exc:
            print(f"[follow_up_draft] _draft_one error {thread['thread_id'][:8]}: {exc}")
            return None

    task_cards = []
    for t in clean_candidates:
        r = await _draft_one(t)
        if isinstance(r, dict):
            task_cards.append(r)
    _t(f"all_drafts ({len(task_cards)}/{len(candidates)} succeeded)", t0)
    return task_cards


def _backfill_null_timestamps(user_id: str, db, svc) -> None:
    """Populate last_user_sent_at for existing open threads that have NULL."""
    null_rows = (
        db.table("follow_up_threads")
        .select("id, thread_id")
        .eq("user_id", user_id)
        .eq("status", "open")
        .is_("last_user_sent_at", "null")
        .not_.is_("thread_id", "null")
        .limit(10)
        .execute()
    ).data or []

    for row in null_rows:
        try:
            thread = svc.users().threads().get(
                userId="me", id=row["thread_id"], format="minimal"
            ).execute()
            sent_msgs = [m for m in thread.get("messages", []) if "SENT" in m.get("labelIds", [])]
            if sent_msgs:
                ts = max(int(m["internalDate"]) for m in sent_msgs) / 1000
                db.table("follow_up_threads").update({
                    "last_user_sent_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                }).eq("id", row["id"]).execute()
        except Exception:
            continue


def _check_draft_validity(user_id: str, db, svc) -> None:
    """
    Verify each open thread's Gmail draft still exists.
    Sent draft → resolve thread. Deleted draft → reset draft_gmail_id silently.
    """
    open_with_draft = (
        db.table("follow_up_threads")
        .select("id, thread_id, draft_gmail_id, last_nudged_at, detected_at")
        .eq("user_id", user_id)
        .eq("status", "open")
        .not_.is_("draft_gmail_id", "null")
        .execute()
    ).data or []

    if not open_with_draft:
        return

    from googleapiclient.errors import HttpError  # noqa: PLC0415

    for row in open_with_draft:
        # Check this specific draft by ID — only act on a definitive 404, not API flakiness
        try:
            svc.users().drafts().get(
                userId="me", id=row["draft_gmail_id"], format="minimal"
            ).execute()
            continue  # draft still exists
        except HttpError as e:
            if e.resp.status != 404:
                continue  # non-404 HttpError (quota, auth, etc.) — don't touch
        except Exception:
            continue  # non-HTTP error — don't touch

        # Definitive 404 — was it sent or deleted?
        sent_after = False
        try:
            thread = svc.users().threads().get(
                userId="me", id=row["thread_id"], format="minimal"
            ).execute()
            sent_msgs = [m for m in thread.get("messages", []) if "SENT" in m.get("labelIds", [])]
            ref_ts = _parse_ts(row.get("last_nudged_at") or row.get("detected_at") or "")
            sent_after = any(int(m["internalDate"]) / 1000 > ref_ts for m in sent_msgs)
        except Exception:
            pass

        if sent_after:
            db.table("follow_up_threads").update({"status": "resolved"}).eq("id", row["id"]).execute()
        else:
            db.table("follow_up_threads").update({
                "draft_gmail_id": None,
                "draft_created_at": None,
            }).eq("id", row["id"]).execute()


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    db = get_admin_db()
    now = datetime.now(timezone.utc)

    svc, memory_context = await asyncio.gather(
        asyncio.to_thread(_get_gmail_service),
        get_proactive_memory_context(user_id, "email communication relationships projects"),
    )

    await asyncio.gather(
        asyncio.to_thread(_backfill_null_timestamps, user_id, db, svc),
        asyncio.to_thread(_check_draft_validity, user_id, db, svc),
    )
    task_cards = await _run_draft_phase(user_id, db, svc, now, memory_context)

    if not task_cards:
        return ProactiveResult(
            text="Follow-up draft — nothing to draft",
            job_type="follow_up_draft",
            deliver=False,
        )
    return ProactiveResult(
        text=f"Follow-up draft — {len(task_cards)} draft{'s' if len(task_cards) != 1 else ''} created",
        job_type="follow_up_draft",
        task_cards=task_cards,
    )

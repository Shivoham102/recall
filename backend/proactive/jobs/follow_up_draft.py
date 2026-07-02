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
from proactive.jobs._followup_judge import judge_followups
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
    Single format=full fetch per draft thread. One response carries bodies AND headers for
    every message, so it replaces the old minimal + metadata (+ conditional last-sent) calls:
      - context_summary: full quote-stripped bodies of the last messages (role-labeled)
      - threading headers (Message-ID, References, Subject) from the last message
      - To/Cc from the last SENT message (the user's own addressing), else the last message
    Returns in_reply_to, references, cc, to, subject, context_summary.
    """
    import email.utils as _eu  # noqa: PLC0415
    from tools.google_services import thread_role_bodies_from_messages  # noqa: PLC0415
    try:
        thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        msgs = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", "0")))
        if not msgs:
            return {"in_reply_to": "", "references": "", "cc": "", "to": "", "subject": "", "context_summary": ""}

        context_summary = thread_role_bodies_from_messages(msgs)

        last_msg = msgs[-1]
        last_sent = next((m for m in reversed(msgs) if "SENT" in m.get("labelIds", [])), None)

        def _hdrs(msg: dict) -> dict:
            return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

        hdrs = _hdrs(last_msg)
        latest_id = hdrs.get("message-id", "")
        subject = hdrs.get("subject", "")
        ref_parts = hdrs.get("references", "").split() if hdrs.get("references") else []
        if latest_id and latest_id not in ref_parts:
            ref_parts.append(latest_id)

        # To/Cc come from the last SENT message (preserves the original behavior: last-sent if a
        # sent message exists — which equals last_msg when the user sent last — else last_msg).
        sent_hdrs = _hdrs(last_sent) if last_sent else hdrs

        cc_pairs = _eu.getaddresses([sent_hdrs.get("cc", "")]) if sent_hdrs.get("cc") else []
        cc_str = ", ".join(addr for _, addr in cc_pairs if addr)
        to_raw = sent_hdrs.get("to", "")
        display_to, addr_to = _eu.parseaddr(to_raw)
        if not addr_to or "@" not in addr_to:
            m = re.search(r"<([^>]+@[^>]+)>", to_raw)
            if m:
                addr_to = m.group(1)
                display_to = to_raw[: m.start()].strip().rstrip(",").strip().strip('"')
        to_str = (
            f"{display_to} <{addr_to}>" if display_to and addr_to
            else addr_to or display_to or ""
        )

        return {
            "in_reply_to": latest_id,
            "references": " ".join(ref_parts),
            "cc": cc_str,
            "to": to_str,
            "subject": subject,
            "context_summary": context_summary,
        }
    except Exception as exc:
        print(f"[follow_up_draft] _get_thread_draft_meta error {thread_id[:8]}: {exc}")
    return {"in_reply_to": "", "references": "", "cc": "", "to": "", "subject": "", "context_summary": ""}


def _get_display_name(svc, user_id: str = "") -> str:
    """Fetch display name for email sign-off. Gmail sendAs primary → DB users.name fallback."""
    try:
        aliases = svc.users().settings().sendAs().list(userId="me").execute()
        for alias in aliases.get("sendAs", []):
            if alias.get("isPrimary"):
                name = alias.get("displayName", "")
                if name:
                    return name
        send_as_list = aliases.get("sendAs", [])
        if send_as_list:
            name = send_as_list[0].get("displayName", "")
            if name:
                return name
    except Exception as exc:
        print(f"[follow_up_draft] _get_display_name sendAs error: {exc}")
    if user_id:
        try:
            row = get_admin_db().table("users").select("name").eq("id", user_id).single().execute()
            name = (row.data or {}).get("name", "")
            if name:
                return name
        except Exception as exc:
            print(f"[follow_up_draft] _get_display_name DB fallback error: {exc}")
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
    author = display_name or "the user"
    # A bare email address (anonymized craigslist / relay senders carry no display name) must
    # NOT be treated as a name, or the model is told to greet "Hi relay@craigslist.org," and
    # leaks its confusion ("I couldn't find a name...") into the draft body. Greet by name only
    # when a real display name is present, and let the model pick the salutation from it so a
    # label like "Sales Team" is handled sensibly instead of becoming "Hi Sales,".
    has_name = bool(counterparty) and "@" not in counterparty
    open_line = (
        f"open with '{greeting}' and the recipient's name, using their first name if "
        f"'{counterparty}' is a person's name (e.g. '{greeting} <first name>,')"
        if has_name
        else f"open with '{greeting},' with no name (no recipient name is available, so do "
             f"not address them by name)"
    )
    resp = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content":
            f"You are ghostwriting an email for {author}.\n"
            f"In the thread context below, messages labeled USER are written BY {author}. "
            f"They are the sender, not the recipient.\n"
            f"Background on {author}: {memory_context or 'none'}\n"
            f"Write a follow-up email body to {counterparty}.\n"
            f"Thread context: {context_summary}\n"
            f"Original commitment/request: {commitment}\n"
            f"Style: {formality} tone, ~{avg_words} words/sentence, {open_line}\n"
            f"Rules: 2-4 sentences, no em dashes, do not identify as AI, "
            f"reference the specific commitment or ask from the thread. "
            f"Write only the email itself. Never mention missing information (a name, date, "
            f"link, etc.) and never ask the reader or anyone else to supply it; if something "
            f"is unknown, write around it. End with exactly: {sign_off}"}],
    )
    return resp.content[0].text.strip()


# Drafting is uncapped but time-boxed: stop before JOB_TIMEOUT_SECONDS (270) so the loop ends
# cleanly between drafts instead of being hard-cancelled mid-write. Candidates are processed in
# slices so a large backlog still produces drafts (meta+judge per slice, not all up front).
DRAFT_BUDGET_SECONDS = 240
DRAFT_SLICE = 15


async def _run_draft_phase(
    user_id: str,
    db,
    svc,
    now: datetime,
    memory_context: str,
    deadline: float,
) -> list[dict]:
    """Draft follow-up emails for qualifying open threads (called only from follow_up_draft.run()).
    `deadline` is a time.monotonic() cutoff measured from run() entry; drafting stops at it."""
    import context  # ContextVar module — context.py:7  # noqa: PLC0415
    from tools.google_services import (  # noqa: PLC0415
        calendar_events_window,
        gmail_fetch_style_samples,
        gmail_reply_draft,
    )

    cutoff = (now - timedelta(hours=48)).isoformat()
    candidates = (
        db.table("follow_up_threads")
        .select("id, thread_id, counterparty, commitment_text, trigger_type")
        .eq("user_id", user_id)
        .eq("status", "open")
        .eq("was_drafted", False)
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

    display_name = await asyncio.to_thread(_get_display_name, svc, user_id)
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
    clean_candidates = list(by_counterparty.values())  # uncapped — bounded by the soft deadline below
    print(f"[follow_up_draft] {len(clean_candidates)} candidates after counterparty dedup")

    async def _draft_one(thread: dict, meta: dict) -> dict | None:
        try:
            t_start = time.perf_counter()
            counterparty = meta["to"] or thread["counterparty"]

            # Guard: skip if no valid email — avoids "Invalid To header" from Gmail
            # Single parseaddr call; regex fallback handles unquoted @ or comma in display name
            display_cp, to_addr = _eu.parseaddr(counterparty)
            if not to_addr or "@" not in to_addr:
                m = re.search(r"<([^>]+@[^>]+)>", counterparty)
                if m:
                    to_addr = m.group(1)
                    display_cp = counterparty[: m.start()].strip().rstrip(",").strip().strip('"')
                else:
                    to_addr = ""
            if not to_addr:
                print(f"[follow_up_draft] skipping {thread['thread_id'][:8]}: no valid email in '{counterparty[:60]}'")
                return None
            cp_for_prompt = display_cp or to_addr

            in_reply_to = meta["in_reply_to"]
            references = meta["references"]
            cc_str = meta["cc"]
            subject = meta.get("subject", "")
            context_summary = meta.get("context_summary") or thread["commitment_text"]
            print(
                f"[follow_up_draft] threading {thread['thread_id'][:8]}: "
                f"in_reply_to={'SET' if in_reply_to else 'EMPTY'} "
                f"subject={subject!r} counterparty_src={'meta' if meta['to'] else 'db'}"
            )

            draft_body = await _haiku_draft(
                context_summary=context_summary,
                commitment=thread["commitment_text"],
                counterparty=cp_for_prompt,
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
                "subject": f"Re: {subject}" if subject else "",
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
                    "was_drafted": True,
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

    # Calendar is fetched once and reused as the judge gate across every slice. Then process
    # candidates in deadline-bounded slices: per slice we fetch thread meta (in parallel), run one
    # calendar-aware judge, resolve the threads it rejects, and draft the survivors. Checking the
    # budget between slices and before each draft means a large backlog still produces drafts
    # (instead of burning the whole budget on an up-front meta/judge pass over everything) and the
    # loop always stops cleanly between drafts — each of which already committed via was_drafted.
    calendar_events = await calendar_events_window()
    t0 = _t("calendar_fetch", t0)

    task_cards: list[dict] = []
    for slice_start in range(0, len(clean_candidates), DRAFT_SLICE):
        if time.monotonic() >= deadline:
            print(f"[follow_up_draft] soft deadline before slice {slice_start}: "
                  f"drafted {len(task_cards)}/{len(clean_candidates)}, rest next run")
            break
        chunk = clean_candidates[slice_start:slice_start + DRAFT_SLICE]
        metas = list(await asyncio.gather(*[
            asyncio.to_thread(_get_thread_draft_meta, svc, t["thread_id"]) for t in chunk
        ]))

        judge_input = [
            {
                "trigger_type": t.get("trigger_type") or "sent_commitment",
                "commitment_text": t.get("commitment_text", ""),
                "context": m.get("context_summary") or t.get("commitment_text", ""),
                "counterparty": t.get("counterparty", ""),
                "subject": m.get("subject", ""),
            }
            for t, m in zip(chunk, metas)
        ]
        keep_flags = await judge_followups(judge_input, calendar_events)

        for t, m, keep in zip(chunk, metas, keep_flags):
            if not keep:
                db.table("follow_up_threads").update({"status": "resolved"}).eq("id", t["id"]).execute()
                print(f"[follow_up_draft] judged no follow-up needed, resolved {t['thread_id'][:8]}")
                continue
            if time.monotonic() >= deadline:
                print(f"[follow_up_draft] soft deadline mid-slice: "
                      f"drafted {len(task_cards)}/{len(clean_candidates)}, rest next run")
                _t(f"all_drafts ({len(task_cards)}/{len(candidates)} succeeded)", t0)
                return task_cards
            r = await _draft_one(t, m)
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
    # Budget measured from here so it covers the up-front gmail/style/backfill work too.
    deadline = time.monotonic() + DRAFT_BUDGET_SECONDS

    svc, memory_context = await asyncio.gather(
        asyncio.to_thread(_get_gmail_service),
        get_proactive_memory_context(user_id, "email communication relationships projects"),
    )

    await asyncio.gather(
        asyncio.to_thread(_backfill_null_timestamps, user_id, db, svc),
        asyncio.to_thread(_check_draft_validity, user_id, db, svc),
    )
    task_cards = await _run_draft_phase(user_id, db, svc, now, memory_context, deadline)

    if not task_cards:
        return ProactiveResult(
            text="Follow-up draft: nothing to draft",
            job_type="follow_up_draft",
            deliver=False,
        )
    return ProactiveResult(
        text=f"Follow-up draft: {len(task_cards)} draft{'s' if len(task_cards) != 1 else ''} created",
        job_type="follow_up_draft",
        task_cards=task_cards,
    )

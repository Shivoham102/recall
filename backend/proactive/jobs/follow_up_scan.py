"""
Follow-up scan — LLM-filtered pipeline (runs daily at 3pm via Vercel cron).

Searches sent mail for commitment language AND inbox for unanswered requests,
runs each candidate through Claude Haiku to confirm a real follow-up is needed,
and tracks open threads in follow_up_threads.
Drafting is handled exclusively by the 6am follow_up_draft job.
"""
import asyncio
import email.utils
import html
import re
import time
from datetime import datetime, timedelta, timezone

from db import get_admin_db
from proactive.memory_context import get_proactive_memory_context
from proactive.runner import ProactiveResult


def _t(label: str, t0: float) -> float:
    elapsed = time.perf_counter() - t0
    print(f"[follow_up_scan] {label}: {elapsed:.1f}s")
    return time.perf_counter()

_COMMITMENT_QUERY = (
    '"follow up" OR "get back to you" OR "I\'ll check" OR "will send"'
    ' OR "check back" OR "following up" OR "circle back"'
)
# Job application detection uses a separate shorter query (_JOB_QUERY) to avoid
# Gmail API timeouts — long OR chains on in:sent take >60s on large mailboxes.
_JOB_QUERY = '"applying for" OR "my application" OR "position at" OR "role at"'
_LOOKBACK_DAYS = 7
_NUDGE_INTERVAL_HOURS = 48

_SENDER_NOISE = re.compile(
    r"(noreply|no-reply|donotreply|do-not-reply|notification|newsletter"
    r"|mailer-daemon|postmaster|bounce|alerts?@|updates?@|support@"
    r"|unsubscribe|marketing|digest|automated|careers@|@email\.|@em\.|@emails\.|@mail\.)",
    re.IGNORECASE,
)
_SUBJECT_NOISE = re.compile(
    r"(application received|thank you for apply|thanks for apply|we received your"
    r"|no action (required|needed)|automatically generated|do not reply"
    r"|your (application|submission)|unsubscribe)",
    re.IGNORECASE,
)


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


def _get_thread_snippets(svc, thread_id: str) -> str:
    """Return last 8 messages as 'USER: snippet / COUNTERPARTY: snippet' lines."""
    try:
        thread = svc.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
        msgs = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", "0")))
        lines = []
        for msg in msgs[-8:]:
            role = "USER" if "SENT" in msg.get("labelIds", []) else "COUNTERPARTY"
            lines.append(f"{role}: {html.unescape(msg.get('snippet', ''))[:300]}")
        return "\n".join(lines)
    except Exception:
        return ""


async def _llm_filter_batch(candidates: list[dict], summaries: dict[str, str]) -> list[bool]:
    """
    Single Haiku call to filter all candidates at once.
    Returns list of bools (True = needs follow-up) in same order as candidates.
    Falls back to False (drop) on any error to avoid polluting DB with false positives.
    """
    from anthropic import AsyncAnthropic  # noqa: PLC0415
    import os  # noqa: PLC0415

    if not candidates:
        return []

    lines = []
    for i, t in enumerate(candidates):
        summary = summaries.get(t["thread_id"], "")
        condition = (
            "USER has NOT replied after the request"
            if t["trigger_type"] == "inbox_awaiting_reply"
            else "COUNTERPARTY has NOT adequately replied to the commitment"
        )
        snippet = summary[:600] if summary else "(no thread data)"
        lines.append(
            f"[{i}] commitment: {t['commitment_text'][:120]}\n"
            f"    condition: {condition}\n"
            f"    thread:\n{snippet}"
        )

    prompt = (
        "For each numbered email thread below, answer YES (needs follow-up) or NO (does not).\n"
        "YES if: user sent a message or application awaiting a substantive reply; "
        "thread has no counterparty reply yet.\n"
        "NO if: meeting scheduled/accepted with confirmation, conversation explicitly concluded, "
        "automated/system email, noreply sender, promotional or marketing email, newsletter.\n"
        "Reply with ONLY a comma-separated list of YES/NO in order. Example: YES,NO,YES\n\n"
        + "\n\n".join(lines)
        + f"\n\nAnswer ({len(candidates)} items):"
    )

    try:
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=20.0,
        )
        answers = [a.strip().upper() for a in resp.content[0].text.strip().split(",")]
        results = []
        for i in range(len(candidates)):
            ans = answers[i] if i < len(answers) else "NO"  # partial response → drop
            results.append(ans.startswith("Y"))
        return results
    except Exception as exc:
        print(f"[follow_up_scan] _llm_filter_batch error: {exc} — dropping all")
        return [False] * len(candidates)


def _auto_yes(candidate: dict, snippet: str) -> bool:
    """sent_commitment threads auto-YES — user sent it, follow-up is implied. LLM only for inbox."""
    return candidate["trigger_type"] == "sent_commitment"


async def _search_raw_candidates(svc) -> list[dict]:
    """Search sent mail + inbox in parallel, fetch details in parallel. Returns raw candidates."""
    after_epoch = int((datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).timestamp())
    cutoff_48h_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
    # Keep category:updates — recruiter and outreach replies land there
    inbox_query = (
        f'in:inbox -category:promotions -category:social '
        f'-from:noreply -from:no-reply -from:notifications after:{after_epoch}'
    )

    # Run Gmail searches sequentially — httplib2 is not thread-safe; concurrent calls on
    # the same service object corrupt the connection pool.
    async def _list_safe(fn, label: str) -> dict:
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=25.0)
            print(f"[follow_up_scan] gmail list {label}: {len(result.get('messages', []))} msgs")
            return result
        except asyncio.TimeoutError:
            print(f"[follow_up_scan] gmail list timeout: {label}")
            return {}
        except Exception as exc:
            print(f"[follow_up_scan] gmail list error {label}: {exc}")
            return {}

    sent_list = await _list_safe(
        lambda: svc.users().messages().list(
            userId="me", q=f"in:sent after:{after_epoch} ({_COMMITMENT_QUERY})", maxResults=20,
        ).execute(),
        "sent_commitment",
    )
    job_list = await _list_safe(
        lambda: svc.users().messages().list(
            userId="me", q=f"in:sent after:{after_epoch} ({_JOB_QUERY})", maxResults=20,
        ).execute(),
        "sent_job",
    )
    # Broad fallback: any recent sent message the keyword queries may have missed
    sent_recent_list = await _list_safe(
        lambda: svc.users().messages().list(
            userId="me", q=f"in:sent after:{after_epoch}", maxResults=15,
        ).execute(),
        "sent_recent",
    )
    inbox_list = await _list_safe(
        lambda: svc.users().messages().list(
            userId="me", q=inbox_query, maxResults=15,
        ).execute(),
        "inbox",
    )

    # Merge all sent results, dedupe by msg id then early-dedup by threadId.
    # Gmail returns reverse-chron, so first occurrence of a threadId = most recent sent msg.
    msg_by_id = {
        m["id"]: m
        for m in (
            sent_list.get("messages", [])
            + job_list.get("messages", [])
            + sent_recent_list.get("messages", [])
        )
    }
    seen_sent_tids: set[str] = set()
    sent_msgs: list[dict] = []
    for m in msg_by_id.values():
        tid = m.get("threadId", m["id"])
        if tid not in seen_sent_tids:
            seen_sent_tids.add(tid)
            sent_msgs.append(m)
        if len(sent_msgs) >= 20:
            break
    inbox_msgs = inbox_list.get("messages", [])[:10]
    print(f"[follow_up_scan] after thread dedup: {len(sent_msgs)} sent threads, {len(inbox_msgs)} inbox msgs")

    async def _get_detail(msg_id: str, hdrs: list[str]) -> dict:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(lambda: svc.users().messages().get(
                    userId="me", id=msg_id, format="metadata", metadataHeaders=hdrs,
                ).execute()),
                timeout=12.0,
            )
        except Exception:
            return {}

    async def _batch(coros, batch_size: int, delay: float) -> list:
        results = []
        items = list(coros)
        for i in range(0, len(items), batch_size):
            results.extend(await asyncio.gather(*items[i:i + batch_size]))
            if i + batch_size < len(items):
                await asyncio.sleep(delay)
        return results

    sent_details = await _batch(
        [_get_detail(m["id"], ["To", "Subject", "Cc"]) for m in sent_msgs],
        batch_size=1, delay=0.0,
    )
    inbox_details = await _batch(
        [_get_detail(m["id"], ["From", "Subject"]) for m in inbox_msgs],
        batch_size=1, delay=0.0,
    )

    seen_threads: set[str] = set()
    threads: list[dict] = []

    for detail in sent_details:
        if not detail:
            continue
        thread_id = detail.get("threadId", "")
        if not thread_id or thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        to_raw = headers.get("To", "")
        cc_raw = headers.get("Cc", "")
        subject = headers.get("Subject", "(no subject)")
        if _SENDER_NOISE.search(to_raw) or _SUBJECT_NOISE.search(subject):
            continue
        snippet = html.unescape(detail.get("snippet", ""))[:200]
        display_to, addr_to = email.utils.parseaddr(to_raw)
        if not addr_to or "@" not in addr_to:
            m = re.search(r"<([^>]+@[^>]+)>", to_raw)
            if m:
                addr_to = m.group(1)
                display_to = to_raw[: m.start()].strip().rstrip(",").strip().strip('"')
        counterparty = (
            f"{display_to} <{addr_to}>" if display_to and addr_to
            else addr_to or display_to or "(unknown)"
        )
        ts_ms = int(detail.get("internalDate", "0"))
        sent_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
        threads.append({
            "thread_id": thread_id, "counterparty": counterparty, "subject": subject,
            "commitment_text": snippet, "sent_at": sent_at, "cc": cc_raw.strip(),
            "trigger_type": "sent_commitment",
        })

    for detail in inbox_details:
        if not detail:
            continue
        ts = int(detail.get("internalDate", "0")) / 1000
        if ts > cutoff_48h_ts:
            continue
        thread_id = detail.get("threadId", "")
        if not thread_id or thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        from_raw = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")
        if _SENDER_NOISE.search(from_raw) or _SUBJECT_NOISE.search(subject):
            continue
        snippet = html.unescape(detail.get("snippet", ""))[:200]
        display_from, addr_from = email.utils.parseaddr(from_raw)
        if not addr_from or "@" not in addr_from:
            m = re.search(r"<([^>]+@[^>]+)>", from_raw)
            if m:
                addr_from = m.group(1)
                display_from = from_raw[: m.start()].strip().rstrip(",").strip().strip('"')
        counterparty = (
            f"{display_from} <{addr_from}>" if display_from and addr_from
            else addr_from or display_from or "(unknown)"
        )
        threads.append({
            "thread_id": thread_id, "counterparty": counterparty, "subject": subject,
            "commitment_text": f"[Needs reply] {subject}: {snippet}",
            "sent_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "cc": "", "trigger_type": "inbox_awaiting_reply",
        })

    return threads


async def run(user_id: str, context_key: str | None = None, user_tz: str = "UTC") -> ProactiveResult:
    db = get_admin_db()
    now = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    print(f"[follow_up_scan] start user={user_id}")

    # ── Phase 1: memory + gmail service + existing threads in parallel ────────
    def _load_existing():
        # Load open + ignored so ignored threads aren't re-inserted on next scan
        return (
            db.table("follow_up_threads")
            .select("id, thread_id, counterparty, commitment_text, nudge_count, last_nudged_at, status, trigger_type, last_user_sent_at, was_drafted, draft_gmail_id, draft_created_at")
            .eq("user_id", user_id)
            .in_("status", ["open", "ignored"])
            .execute()
        )

    memory_context, svc, existing_res = await asyncio.gather(
        get_proactive_memory_context(user_id, "follow up relationships projects commitments"),
        asyncio.to_thread(_get_gmail_service),
        asyncio.to_thread(_load_existing),
    )
    existing_open = existing_res.data or []
    existing_by_thread: dict[str, dict] = {
        row["thread_id"]: row for row in existing_open if row.get("thread_id")
    }
    open_count = sum(1 for r in existing_open if r.get("status") == "open")
    t0 = _t(f"init_parallel (memory+gmail+db, {open_count} open, {len(existing_open) - open_count} ignored)", t0)

    # ── Phase 2: inbox closure check + raw candidate search in parallel ───────
    inbox_open = [r for r in existing_open if r.get("trigger_type") == "inbox_awaiting_reply" and r.get("thread_id") and r.get("status") == "open"]

    cls_sem = asyncio.Semaphore(3)

    async def _check_closure(row: dict) -> str | None:
        async with cls_sem:
            try:
                snippets = await asyncio.to_thread(_get_thread_snippets, svc, row["thread_id"])
                last_line = snippets.strip().split("\n")[-1] if snippets.strip() else ""
                if last_line.startswith("USER:"):
                    db.table("follow_up_threads").update({"status": "resolved"}).eq("id", row["id"]).execute()
                    return row["thread_id"]
            except Exception:
                pass
            return None

    # Run closure checks first (sequential with search) — httplib2 is not thread-safe;
    # concurrent use of the same svc object corrupts the connection pool.
    closure_results = await asyncio.gather(*[_check_closure(r) for r in inbox_open])
    for tid in closure_results:
        if tid:
            existing_by_thread.pop(tid, None)
    t0 = _t(f"closure_check ({len(inbox_open)} inbox)", t0)

    print(f"[follow_up_scan] starting search")
    try:
        raw_candidates = await _search_raw_candidates(svc)
    except Exception as exc:
        print(f"[follow_up_scan] search crashed: {type(exc).__name__}: {exc}")
        raw_candidates = []
    t0 = _t(f"search ({len(raw_candidates)} candidates)", t0)

    for _c in raw_candidates:
        print(f"[follow_up_scan] candidate: type={_c['trigger_type']} counterparty={_c['counterparty'][:40]} snippet={_c['commitment_text'][:60]}")

    # sent_commitment → auto-YES (user sent it, no thread fetch needed).
    # inbox_awaiting_reply → LLM filter using message snippet as context.
    auto_yes_flags = [_auto_yes(t, "") for t in raw_candidates]
    inbox_batch = [t for t, ay in zip(raw_candidates, auto_yes_flags) if not ay]
    summaries = {t["thread_id"]: t["commitment_text"] for t in inbox_batch}
    llm_results = await _llm_filter_batch(inbox_batch, summaries)
    llm_map = {t["thread_id"]: f for t, f in zip(inbox_batch, llm_results)}
    keep_flags = [ay or llm_map.get(t["thread_id"], False) for t, ay in zip(raw_candidates, auto_yes_flags)]
    commitments = [t for t, keep in zip(raw_candidates, keep_flags) if keep]
    auto_count = sum(auto_yes_flags)
    print(f"[follow_up_scan] auto_yes={auto_count}/{len(raw_candidates)}, llm_inbox={len(inbox_batch)}")
    t0 = _t(f"filter ({len(commitments)}/{len(raw_candidates)} kept, {auto_count} auto-YES)", t0)

    # ── Insert new / nudge existing ───────────────────────────────────────────
    new_items: list[dict] = []
    nudge_due: list[dict] = []
    nudge_interval = timedelta(hours=_NUDGE_INTERVAL_HOURS)

    for commitment in commitments:
        thread_id = commitment["thread_id"]
        if thread_id in existing_by_thread:
            row = existing_by_thread[thread_id]
            if row.get("status") == "ignored":
                continue  # already ignored — don't re-surface or nudge
            # Reset was_drafted if conversation advanced past the deleted draft.
            # Uses commitment["sent_at"] (fresh from this scan) not stale last_user_sent_at.
            if row.get("was_drafted") and not row.get("draft_gmail_id"):
                draft_ts = row.get("draft_created_at")
                sent_ts = commitment.get("sent_at")
                if draft_ts and sent_ts and sent_ts > draft_ts:
                    db.table("follow_up_threads").update({"was_drafted": False}).eq("id", row["id"]).execute()
                    row["was_drafted"] = False
            last_nudged = row.get("last_nudged_at")
            if last_nudged:
                try:
                    ln_dt = datetime.fromisoformat(last_nudged.replace("Z", "+00:00"))
                    if (now - ln_dt.astimezone(timezone.utc)) < nudge_interval:
                        continue
                except Exception:
                    pass
            new_nudge_count = row["nudge_count"] + 1
            update_fields: dict = {
                "nudge_count": new_nudge_count,
                "last_nudged_at": now.isoformat(),
            }
            if new_nudge_count >= 5:
                update_fields["status"] = "ignored"
            # Refresh sent_at if newer
            if commitment.get("sent_at"):
                existing_ts = row.get("last_user_sent_at") or ""
                if not existing_ts or commitment["sent_at"] > existing_ts:
                    update_fields["last_user_sent_at"] = commitment["sent_at"]
            db.table("follow_up_threads").update(update_fields).eq("id", row["id"]).execute()
            if update_fields.get("status") != "ignored":
                nudge_due.append({**row, **update_fields})
        else:
            try:
                insert_res = (
                    db.table("follow_up_threads")
                    .insert({
                        "user_id": user_id,
                        "thread_id": thread_id,
                        "counterparty": commitment["counterparty"],
                        "commitment_text": commitment["commitment_text"][:300],
                        "source": "email",
                        "detected_at": now.isoformat(),
                        "last_user_sent_at": commitment.get("sent_at"),
                        "trigger_type": commitment["trigger_type"],
                    })
                    .execute()
                )
                if insert_res.data:
                    new_items.append({**commitment, "id": insert_res.data[0]["id"]})
            except Exception as e:
                err_str = str(e).lower()
                if "unique" in err_str or "duplicate" in err_str:
                    print(f"[follow_up_scan] concurrent insert for {thread_id[:8]} — skipping")
                else:
                    print(f"[follow_up_scan] insert error {thread_id[:8]}: {e}")

    all_pending = new_items + nudge_due
    if memory_context:
        all_pending = sorted(all_pending, key=lambda item: _memory_score(item, memory_context), reverse=True)

    scan_task_cards = [
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

    _t(f"db_insert_update ({len(new_items)} new, {len(nudge_due)} nudged)", t0)

    if not scan_task_cards:
        return ProactiveResult(
            text="Follow-up scan complete — nothing pending",
            job_type="follow_up_scan",
            deliver=False,
        )

    count = len(scan_task_cards)
    new_count = len(new_items)
    text = f"Follow-up scan — {count} pending follow-up{'s' if count != 1 else ''}"
    if new_count:
        text += f" ({new_count} new)"

    return ProactiveResult(
        text=text,
        job_type="follow_up_scan",
        task_cards=scan_task_cards,
        metadata={"memory_context_used": bool(memory_context)},
    )

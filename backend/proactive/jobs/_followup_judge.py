"""
Shared calendar-aware follow-up judge.

Given email-thread candidates (with full-body context) and the user's calendar, decides per
candidate whether a follow-up is still genuinely needed. Used by both follow_up_scan (gate
discovery/nudging) and follow_up_draft (final gate before writing a draft) so the logic and
prompt live in one place.

Each candidate dict needs:
    trigger_type    "sent_commitment" | "inbox_awaiting_reply"
    commitment_text short description of what's owed / awaited
    context         full-body thread text (role-labeled); may be "" if the fetch failed
    counterparty    (optional) for calendar matching by people
    subject         (optional)

Returns list[bool] in candidate order — True = keep (needs follow-up).
"""
import asyncio
import os

_MODEL = "claude-haiku-4-5-20251001"
_BATCH_SIZE = 12          # hard cap so the YES/NO list can't truncate
_JUDGE_TIMEOUT = 25.0


def _fallback_keep(candidate: dict) -> bool:
    """Pre-LLM behavior: sent commitments are kept, inbox-awaiting are dropped.

    Used whenever the model can't be trusted for an item (call error, or a missing/short
    answer), so a Haiku hiccup never silently drops a genuine sent-commitment follow-up.
    """
    return candidate.get("trigger_type") == "sent_commitment"


def _format_calendar(events: list[dict]) -> str:
    if not events:
        return "CALENDAR: (none available)"
    lines = ["CALENDAR (the user's events, recent past → upcoming):"]
    for ev in events[:60]:
        guests = ", ".join(ev.get("attendees", [])[:6])
        parts = [f"{(ev.get('start') or '')[:16]} {ev.get('title', '(no title)')}"]
        if ev.get("organizer"):
            parts.append(f"organizer: {ev['organizer']}")
        if guests:
            parts.append(f"guests: {guests}")
        if ev.get("description"):
            parts.append(ev["description"])
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def _condition(candidate: dict) -> str:
    return (
        "USER has NOT replied after the request"
        if candidate.get("trigger_type") == "inbox_awaiting_reply"
        else "COUNTERPARTY has NOT adequately replied to the commitment"
    )


def _build_prompt(batch: list[dict], calendar_block: str) -> str:
    blocks = []
    for i, c in enumerate(batch):
        ctx = c.get("context") or c.get("commitment_text", "") or "(no thread data)"
        header = []
        if c.get("counterparty"):
            header.append(f"with: {c['counterparty']}")
        if c.get("subject"):
            header.append(f"subject: {c['subject']}")
        blocks.append(
            f"[{i}] " + " | ".join(header) + "\n"
            f"    owed/awaited: {(c.get('commitment_text') or '')[:200]}\n"
            f"    condition: {_condition(c)}\n"
            f"    thread:\n{ctx}"
        )
    return (
        "You decide which email threads still need a follow-up from the user.\n\n"
        f"{calendar_block}\n\n"
        "Answer YES only if a substantive reply is genuinely still owed and nothing already "
        "resolves it.\n"
        "Answer NO if any of these hold:\n"
        "- the other party already substantively answered or fulfilled the ask;\n"
        "- the conversation reached a natural close (e.g. thanks/closing exchanged, decision made);\n"
        "- a meeting, call, or interview the thread was arranging now appears on the CALENDAR "
        "above. Match the calendar by company, role, people, or topic, NOT by exact email "
        "address (e.g. a recruiter thread is resolved by interview events for that company/role "
        "even when the calendar organizer is a scheduling system rather than the recruiter);\n"
        "- automated/system/noreply/marketing/newsletter mail.\n\n"
        "Reply with ONLY a comma-separated list of YES/NO in order, one per thread. "
        "Example: YES,NO,YES\n\n"
        + "\n\n".join(blocks)
        + f"\n\nAnswer ({len(batch)} items):"
    )


async def _judge_batch(client, batch: list[dict], calendar_block: str) -> list[bool]:
    try:
        resp = await asyncio.wait_for(
            client.messages.create(
                model=_MODEL,
                max_tokens=max(50, len(batch) * 4),
                messages=[{"role": "user", "content": _build_prompt(batch, calendar_block)}],
            ),
            timeout=_JUDGE_TIMEOUT,
        )
        answers = [a.strip().upper() for a in resp.content[0].text.strip().split(",")]
    except Exception as exc:
        print(f"[followup_judge] batch error: {exc} - per-trigger fallback")
        return [_fallback_keep(c) for c in batch]

    results = []
    for i, c in enumerate(batch):
        if i < len(answers) and answers[i]:
            results.append(answers[i].startswith("Y"))
        else:
            # missing / malformed answer → same fallback as a call failure
            results.append(_fallback_keep(c))
    return results


async def judge_followups(candidates: list[dict], calendar_events: list[dict]) -> list[bool]:
    """Judge all candidates (batched). Returns keep-flags in candidate order."""
    if not candidates:
        return []

    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except Exception as exc:
        # Missing key / import failure — never crash the job; use the per-trigger fallback.
        print(f"[followup_judge] client init failed: {exc} - per-trigger fallback")
        return [_fallback_keep(c) for c in candidates]

    calendar_block = _format_calendar(calendar_events)
    results: list[bool] = []
    for i in range(0, len(candidates), _BATCH_SIZE):
        batch = candidates[i:i + _BATCH_SIZE]
        results.extend(await _judge_batch(client, batch, calendar_block))
    return results

import type { AgentChat, AgentTurn, LastCaptureMeta } from "../types/agentTurn";

function nowIso() {
  return new Date().toISOString();
}

function sanitizeTurns(raw: unknown): AgentTurn[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item) => item && typeof item === "object")
    .map((item) => item as Record<string, unknown>)
    .map((item) => ({
      id: typeof item.id === "string" ? item.id : crypto.randomUUID(),
      role:
        item.role === "assistant" || item.role === "system" || item.role === "user" || item.role === "proactive"
          ? item.role
          : "system",
      text: typeof item.text === "string" ? item.text : "",
      intentType: typeof item.intentType === "string" ? item.intentType : undefined,
      steps: Array.isArray(item.steps) ? (item.steps as AgentTurn["steps"]) : undefined,
      emailCards: Array.isArray(item.emailCards) ? (item.emailCards as AgentTurn["emailCards"]) : undefined,
      calendarCards: Array.isArray(item.calendarCards) ? (item.calendarCards as AgentTurn["calendarCards"]) : undefined,
      taskCards: Array.isArray(item.taskCards) ? (item.taskCards as AgentTurn["taskCards"]) : undefined,
      morningBriefDraftCards: Array.isArray(item.morningBriefDraftCards)
        ? (item.morningBriefDraftCards as AgentTurn["morningBriefDraftCards"])
        : undefined,
      pending: typeof item.pending === "boolean" ? item.pending : undefined,
      timestamp: typeof item.timestamp === "string" ? item.timestamp : undefined,
      audioB64: typeof item.audioB64 === "string" ? item.audioB64 : undefined,
    }));
}

const DRAFT_BRIEF_MATCH_WINDOW_MS = 2 * 60 * 60 * 1000;

function isoToMillis(iso?: string): number | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.getTime();
}

function taskCardKey(card: NonNullable<AgentTurn["taskCards"]>[number]): string {
  const id = card.id?.trim();
  if (id) return `id:${id}`;
  return `fallback:${card.intent_type}|${card.content}|${card.created_at}|${card.link ?? ""}`;
}

export function coalesceProactiveTurns(turns: AgentTurn[]): AgentTurn[] {
  if (turns.length === 0) return turns;

  const nextTurns = turns.map((turn) => {
    if (turn.intentType === "morning_brief") {
      return { ...turn, morningBriefDraftCards: [] as NonNullable<AgentTurn["morningBriefDraftCards"]> };
    }
    return { ...turn };
  });

  const morningBriefs: Array<{ idx: number; ts: number }> = [];
  for (let i = 0; i < nextTurns.length; i += 1) {
    const turn = nextTurns[i];
    if (turn.intentType !== "morning_brief") continue;
    const ts = isoToMillis(turn.timestamp);
    if (ts === null) continue;
    morningBriefs.push({ idx: i, ts });
  }

  for (const turn of nextTurns) {
    if (turn.intentType !== "follow_up_draft") continue;
    const draftTs = isoToMillis(turn.timestamp);
    if (draftTs === null) continue;
    let morningIdx: number | null = null;
    let smallestDiff = Number.POSITIVE_INFINITY;
    for (const candidate of morningBriefs) {
      const diff = Math.abs(candidate.ts - draftTs);
      if (diff > DRAFT_BRIEF_MATCH_WINDOW_MS) continue;
      if (diff < smallestDiff) {
        smallestDiff = diff;
        morningIdx = candidate.idx;
      }
    }
    if (morningIdx === null) continue;

    const cards = turn.taskCards ?? [];
    if (cards.length === 0) continue;
    const morningTurn = nextTurns[morningIdx];
    const existing = morningTurn.morningBriefDraftCards ?? [];
    const seen = new Set(existing.map(taskCardKey));
    const merged = [...existing];
    for (const card of cards) {
      const dedupeKey = taskCardKey(card);
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      merged.push(card);
    }
    morningTurn.morningBriefDraftCards = merged;
  }

  return nextTurns.filter((turn) => {
    if (turn.intentType === "follow_up_scan") return false;
    if (turn.intentType === "follow_up_draft") return false;
    return true;
  });
}

/** Hydrate a row from Supabase; `title` is only the persisted value (null if none). */
export function normalizeStoredAgentChat(raw: Record<string, unknown>): AgentChat {
  const turns = sanitizeTurns(raw.turns);
  let storedTitle = typeof raw.title === "string" ? raw.title : null;
  if (storedTitle === "...") storedTitle = null;
  return {
    id: String(raw.id),
    user_id: String(raw.user_id),
    agent_session_id: String(raw.agent_session_id),
    title: storedTitle,
    turns,
    last_capture: (raw.last_capture as LastCaptureMeta | null) ?? null,
    archived_at: typeof raw.archived_at === "string" ? raw.archived_at : null,
    created_at: String(raw.created_at ?? nowIso()),
    updated_at: String(raw.updated_at ?? nowIso()),
    is_proactive_inbox: raw.is_proactive_inbox === true,
  };
}

/** First non-empty user utterance (for title API + display fallback). */
export function firstUserMessageText(turns: AgentTurn[]): string | null {
  for (const t of turns) {
    if (t.role !== "user") continue;
    const compact = t.text.replace(/\s+/g, " ").trim();
    if (!compact || compact === "...") continue;
    return compact;
  }
  return null;
}

/** Truncated first user line when DB has no title (sidebar until LLM title lands). */
export function fallbackSidebarTitleFromTurns(turns: AgentTurn[]): string | null {
  const full = firstUserMessageText(turns);
  if (!full) return null;
  return full.length > 48 ? `${full.slice(0, 45)}…` : full;
}

export function sidebarDisplayTitle(chat: Pick<AgentChat, "title" | "turns">): string {
  const stored = chat.title?.trim();
  if (stored) return stored;
  return fallbackSidebarTitleFromTurns(chat.turns) ?? "New chat";
}

/** Prefill for rename dialog (no literal "New chat" placeholder). */
export function chatRenameInitialDraft(chat: Pick<AgentChat, "title" | "turns">): string {
  const stored = chat.title?.trim();
  if (stored) return stored;
  return fallbackSidebarTitleFromTurns(chat.turns) ?? "";
}

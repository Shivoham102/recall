import { getAuthHeader } from "../hooks/useAuth";
import { getBase } from "./backend";
import { supabase } from "./supabase";
import * as audioLevel from "./audioLevel";
import type { AgentChat } from "../types/agentTurn";
import { normalizeStoredAgentChat } from "../utils/agentChatDisplay";

export type StreamEvent =
  | { type: "transcript"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_call"; name: string; input: unknown }
  | { type: "tool_result"; name: string; summary: string; data: Record<string, unknown> }
  | { type: "token"; text: string }
  | { type: "ack_audio"; audio_base64: string; text: string }
  | { type: "ack_audio_chunk"; data: string }
  | { type: "ack_audio_done"; text: string }
  | { type: "spoken"; text: string }
  | { type: "metadata"; intent_type: string; should_store: boolean; due_hint: string | null; reminder_text: string | null; awaiting_clarification: boolean }
  | { type: "stored"; item_id: string | null; due_at: string | null }
  | { type: "item_updated"; item_id: unknown; due_at: unknown }
  | { type: "audio"; audio_base64: string }
  | { type: "audio_chunk"; data: string }
  | { type: "audio_done" }
  | { type: "error"; message: string; }
  | { type: "done" };

export async function authenticatedFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const withAuth = async () => ({
    ...init,
    headers: {
      ...(init.headers ?? {}),
      ...(await getAuthHeader()),
    },
  });

  let res = await fetch(input, await withAuth());
  if (res.status !== 401) return res;

  await supabase.auth.refreshSession();
  res = await fetch(input, await withAuth());
  return res;
}

async function* _streamEvents(form: FormData, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const res = await authenticatedFetch(`${await getBase()}/capture/stream`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => String(res.status));
    throw new Error(`capture/stream failed: ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    let result: ReadableStreamReadResult<Uint8Array>;
    try {
      result = await reader.read();
    } catch (e) {
      if ((e as DOMException).name === "AbortError") return;
      throw e;
    }
    if (result.done) break;
    buffer += decoder.decode(result.value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      if (part.startsWith("data: ")) {
        try {
          yield JSON.parse(part.slice(6)) as StreamEvent;
        } catch {
          // skip malformed event
        }
      }
    }
  }
}

export async function* captureStream(
  audioBlob: Blob,
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const form = new FormData();
  form.append("audio", audioBlob, "recording.webm");
  form.append("session_id", sessionId);
  form.append("timezone", Intl.DateTimeFormat().resolvedOptions().timeZone);
  yield* _streamEvents(form, signal);
}

export async function* captureStreamText(
  text: string,
  sessionId: string,
): AsyncGenerator<StreamEvent> {
  const form = new FormData();
  form.append("text", text);
  form.append("session_id", sessionId);
  form.append("timezone", Intl.DateTimeFormat().resolvedOptions().timeZone);
  yield* _streamEvents(form);
}

export interface Recurrence {
  freq: "daily" | "weekdays" | "weekly";
  time: string; // "HH:MM" 24h wall-clock
  days?: number[]; // weekly only, 0=Mon..6=Sun
  tz: string;
}

export interface RecallItem {
  id: string;
  content: string;
  intent_type: string;
  status: string;
  created_at: string;
  updated_at: string;
  due_hint: string | null;
  due_at?: string | null;
  recurrence?: Recurrence | null;
  reminded_at?: string | null;
  display_text?: string;
}

export interface MemoryProfile {
  configured: boolean;
  enabled: boolean;
  status: string;
  processing_hint?: string;
  profile: {
    static: string[];
    dynamic: string[];
  };
  relevant_memories: string[];
}

export interface PendingReminder {
  id: string;
  content: string;
  intent_type: string;
  due_at: string;
}

/** Short LLM title from the chat's first user message only (backend `/capture/suggest-title`). */
export async function suggestAgentChatTitle(firstUserMessage: string): Promise<string | null> {
  const text = firstUserMessage.trim();
  if (!text) return null;
  const res = await authenticatedFetch(`${await getBase()}/capture/suggest-title`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) return null;
  const data = (await res.json()) as { title?: string | null };
  const t = typeof data.title === "string" ? data.title.trim() : "";
  return t.length > 0 ? t : null;
}


export async function getItems(params?: {
  status?: string;
  has_due_hint?: boolean;
  limit?: number;
}): Promise<RecallItem[]> {
  const url = new URL(`${await getBase()}/items`);
  if (params?.status) url.searchParams.set("status", params.status);
  if (params?.has_due_hint !== undefined)
    url.searchParams.set("has_due_hint", String(params.has_due_hint));
  if (params?.limit) url.searchParams.set("limit", String(params.limit));
  const res = await authenticatedFetch(url.toString());
  if (!res.ok) throw new Error(`getItems failed: ${res.status}`);
  return res.json();
}

export async function updateItem(
  id: string,
  update: { status?: string; due_hint?: string; due_at?: string; recurrence?: Recurrence; clear_recurrence?: boolean; timezone?: string },
): Promise<RecallItem> {
  const res = await authenticatedFetch(`${await getBase()}/items/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error(`updateItem failed: ${res.status}`);
  return res.json();
}

export async function deleteItem(id: string): Promise<{ ok: boolean }> {
  const res = await authenticatedFetch(`${await getBase()}/items/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`deleteItem failed: ${res.status}`);
  return res.json();
}

export interface AgentSuggestion {
  id: string;
  kind: "recurring_reminder" | "neglected_goal";
  title: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export async function getSuggestions(): Promise<AgentSuggestion[]> {
  const res = await authenticatedFetch(`${await getBase()}/agent/suggestions`);
  if (!res.ok) throw new Error(`getSuggestions failed: ${res.status}`);
  const data = (await res.json()) as { suggestions?: AgentSuggestion[] };
  return data.suggestions ?? [];
}

export async function acceptSuggestion(id: string): Promise<void> {
  const res = await authenticatedFetch(`${await getBase()}/agent/suggestions/${id}/accept`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`acceptSuggestion failed: ${res.status}`);
}

export async function dismissSuggestion(id: string): Promise<void> {
  const res = await authenticatedFetch(`${await getBase()}/agent/suggestions/${id}/dismiss`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`dismissSuggestion failed: ${res.status}`);
}

export interface LearnedHabit {
  id: string;
  content: string;
  recurrence: Recurrence | null;
}

export interface LearnedProfile {
  auto_brief: string[];
  habits: LearnedHabit[];
  suggestions: { accepted: number; dismissed: number; pending: number; total: number };
  google_reauth_required: boolean;
}

export async function getLearned(): Promise<LearnedProfile> {
  const res = await authenticatedFetch(`${await getBase()}/profile/learned`);
  if (!res.ok) throw new Error(`getLearned failed: ${res.status}`);
  return res.json();
}

export async function getMemoryProfile(): Promise<MemoryProfile> {
  const res = await authenticatedFetch(`${await getBase()}/memory/profile`);
  if (!res.ok) throw new Error(`memory/profile failed: ${res.status}`);
  return res.json();
}

export async function clearMemory(): Promise<{ ok: boolean; status: string }> {
  const res = await authenticatedFetch(`${await getBase()}/memory/clear`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`memory/clear failed: ${res.status}`);
  return res.json();
}

export interface MemoryItem {
  id: string;
  text: string;
  status?: string | null;
  source?: string | null;
  category?: string | null;
  created_at?: string | null;
}

export async function listMemoryItems(): Promise<{ ok: boolean; status: string; items: MemoryItem[] }> {
  const res = await authenticatedFetch(`${await getBase()}/memory/items`);
  if (!res.ok) throw new Error(`memory/items failed: ${res.status}`);
  return res.json();
}

export async function deleteMemoryItem(id: string): Promise<{ ok: boolean; status: string }> {
  const res = await authenticatedFetch(`${await getBase()}/memory/items/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`memory/items delete failed: ${res.status}`);
  return res.json();
}

export type AudioStartStatus = "started" | "rejected" | "error";
export type AudioFinishStatus = "ended" | "rejected" | "error";

export interface AudioPlaybackHandle {
  started: Promise<AudioStartStatus>;
  finished: Promise<AudioFinishStatus>;
}

let _audioQueue: Promise<void> = Promise.resolve();
// Bumped by stopCurrentAudio() to cancel any clips still queued; _currentStop stops
// the one currently playing. Used for barge-in (interrupt a response/announcement).
let _audioGen = 0;
let _currentStop: (() => void) | null = null;

/** Stop the clip currently playing and cancel everything still queued. */
export function stopCurrentAudio(): void {
  _audioGen++;
  _currentStop?.();
  _currentStop = null;
}

export function playAudio(base64mp3: string, onEnd?: () => void): AudioPlaybackHandle {
  let resolveStarted!: (value: AudioStartStatus) => void;
  let resolveFinished!: (value: AudioFinishStatus) => void;
  const started = new Promise<AudioStartStatus>((resolve) => { resolveStarted = resolve; });
  const finished = new Promise<AudioFinishStatus>((resolve) => { resolveFinished = resolve; });
  const myGen = _audioGen;

  _audioQueue = _audioQueue.then(
    () =>
      new Promise<void>((resolveQueue) => {
        // Cancelled by a barge-in before our turn in the queue — skip silently.
        if (myGen !== _audioGen) {
          resolveStarted("rejected");
          resolveFinished("rejected");
          resolveQueue();
          return;
        }
        const audio = new Audio(`data:audio/mpeg;base64,${base64mp3}`);
        audioLevel.attachElement(audio); // pulse the orb with spoken audio
        let startedResolved = false;
        let finishedResolved = false;

        const resolveStartOnce = (status: AudioStartStatus) => {
          if (startedResolved) return;
          startedResolved = true;
          resolveStarted(status);
        };

        const finish = (status: AudioFinishStatus) => {
          if (finishedResolved) return;
          finishedResolved = true;
          _currentStop = null;
          if (!startedResolved) resolveStartOnce(status === "ended" ? "started" : status);
          onEnd?.();
          resolveFinished(status);
          resolveQueue();
        };

        // Lets stopCurrentAudio() halt this clip mid-play. pause() fires no event
        // here (only "ended"/"error" are listened to), so finish() runs exactly once.
        _currentStop = () => { audio.pause(); finish("ended"); };

        audio.addEventListener("ended", () => finish("ended"));
        audio.addEventListener("error", (e) => {
          console.error("Audio playback error:", e);
          resolveStartOnce("error");
          finish("error");
        });
        audio.play()
          .then(() => resolveStartOnce("started"))
          .catch((err) => {
            console.error("Audio play() rejected:", err);
            resolveStartOnce("rejected");
            finish("rejected");
          });
      }),
  );

  return { started, finished };
}

export interface DueReminder {
  id: string;
  content: string;
  intent_type: string;
  audio_base64: string;
}

export async function getPendingReminders(): Promise<PendingReminder[]> {
  const res = await authenticatedFetch(`${await getBase()}/reminders/pending`);
  if (!res.ok) throw new Error(`reminders/pending failed: ${res.status}`);
  return res.json();
}

export async function checkDueReminders(silent = false): Promise<DueReminder[]> {
  const res = await authenticatedFetch(`${await getBase()}/reminders/due${silent ? "?silent=1" : ""}`);
  if (!res.ok) throw new Error(`reminders/due failed: ${res.status}`);
  return res.json();
}


export async function markRemindersAsMissed(): Promise<{ id: string; content: string }[]> {
  const res = await authenticatedFetch(`${await getBase()}/reminders/mark-missed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(`reminders/mark-missed failed: ${res.status}`);
  const data: { items: { id: string; content: string }[] } = await res.json();
  return data.items;
}

// ── Proactive stream ──────────────────────────────────────────────────────────

export interface ProactiveJobResult {
  text: string;
  email_cards: unknown[];
  calendar_cards: unknown[];
  task_cards: unknown[];
  metadata?: Record<string, unknown>;
}

export type ProactiveStreamEvent =
  | { type: "connected"; proactive_chat_id: string }
  | { type: "proactive_job"; id: string; job_type: string; result: ProactiveJobResult; audio_b64?: string | null; proactive_chat_id: string; timestamp: string; started_at?: string; finished_at?: string | null }
  | { type: "heartbeat" }
  | { type: "error"; message: string };

export interface ProactiveEventHandlingResult {
  markSeen?: boolean;
}

/** Raw proactive_jobs row as delivered by Supabase Realtime / the init backlog. */
interface ProactiveJobRow {
  id: string;
  job_type: string;
  result: ProactiveJobResult | null;
  status: string;
  delivered: boolean;
  started_at: string;
  finished_at: string | null;
}

interface ProactiveInitResponse {
  proactive_chat_id: string;
  jobs: ProactiveJobRow[];
}

/** Startup/reconnect handshake: inbox chat id + undelivered backlog; bumps last_checkin_at. */
async function initProactive(): Promise<ProactiveInitResponse> {
  const res = await authenticatedFetch(`${await getBase()}/agent/proactive/init`);
  if (!res.ok) throw new Error(`proactive/init failed: ${res.status}`);
  return res.json() as Promise<ProactiveInitResponse>;
}

/** Fetch the morning-brief announcement audio on demand (Realtime rows carry no audio). */
export async function fetchProactiveAnnounceAudio(): Promise<string | null> {
  try {
    const res = await authenticatedFetch(`${await getBase()}/agent/proactive/announce-audio`);
    if (!res.ok) return null;
    const data = (await res.json()) as { audio_b64?: string | null };
    return data.audio_b64 ?? null;
  } catch {
    return null;
  }
}

/** TTS for the quiet-clear nudge ("You have N notifications"), spoken once the call ends. */
export async function fetchNudgeAudio(n: number): Promise<string | null> {
  try {
    const res = await authenticatedFetch(`${await getBase()}/agent/proactive/nudge-audio?n=${encodeURIComponent(String(n))}`);
    if (!res.ok) return null;
    const data = (await res.json()) as { audio_b64?: string | null };
    return data.audio_b64 ?? null;
  } catch {
    return null;
  }
}

/**
 * Subscribe to proactive job delivery via Supabase Realtime. Calls `onEvent` for
 * each event (`connected`, then `proactive_job`s). Returns a cancel function.
 *
 * Delivery state lives in the DB (`proactive_jobs.delivered`), not the transport:
 * the channel pushes live changes, while `/agent/proactive/init` drains any
 * backlog generated while offline. The drain runs in the SUBSCRIBED callback —
 * which fires on the initial attach and every reconnect — so the channel is
 * always attached before we fetch (no missed rows) and reconnects re-drain.
 * Already-seen job IDs are tracked in sessionStorage to prevent duplicate delivery.
 */
export function connectProactiveStream(
  onEvent: (event: ProactiveStreamEvent) => void | ProactiveEventHandlingResult | Promise<void | ProactiveEventHandlingResult>,
): () => void {
  let cancelled = false;
  let channel: ReturnType<typeof supabase.channel> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let checkinTimer: ReturnType<typeof setInterval> | undefined;
  // Set inside connect(); lets the stable onFocus handler trigger a full backlog drain.
  let drain: (() => Promise<void>) | null = null;

  const SEEN_KEY = "recall_proactive_seen_ids";

  function getSeenIds(): Set<string> {
    try {
      const raw = sessionStorage.getItem(SEEN_KEY);
      return new Set(raw ? (JSON.parse(raw) as string[]) : []);
    } catch {
      return new Set();
    }
  }

  function markSeen(id: string): void {
    const ids = getSeenIds();
    ids.add(id);
    try {
      sessionStorage.setItem(SEEN_KEY, JSON.stringify(Array.from(ids)));
    } catch { /* quota exceeded — ignore */ }
  }

  // On focus: re-drain the backlog (also bumps last_checkin_at via initProactive inside
  // drain). This replays any job that was returned markSeen:false because the inbox chat
  // wasn't ready yet — otherwise it would wait for a channel reconnect, which can be hours.
  // handleRow is idempotent (seenIds + delivered gate), so re-draining is safe.
  const onFocus = () => { void drain?.().catch(() => {}); };

  async function connect() {
    if (cancelled) return;
    const { data: { session } } = await supabase.auth.getSession();
    const uid = session?.user?.id;
    if (!uid) {
      // No session yet → don't build "user_id=eq.undefined"; retry once available.
      retryTimer = setTimeout(() => { void connect(); }, 5000);
      return;
    }

    // proactiveChatId/pending persist across this channel's reconnects (the
    // subscribe callback fires repeatedly). A live row can arrive after SUBSCRIBED
    // but before init returns → buffer until proactiveChatId is set.
    let proactiveChatId: string | null = null;
    const pending: ProactiveJobRow[] = [];

    async function handleRow(row: ProactiveJobRow): Promise<void> {
      if (proactiveChatId === null) { pending.push(row); return; }   // race: live row before init
      if (row.status !== "done" || row.delivered) return;            // uniform gate
      if (getSeenIds().has(row.id)) return;
      const result = await onEvent({
        type: "proactive_job",
        id: row.id,
        job_type: row.job_type,
        result: (row.result ?? {}) as ProactiveJobResult,
        audio_b64: null,                       // not on the row; fetched downstream
        proactive_chat_id: proactiveChatId,    // injected from init — not a DB column
        timestamp: row.finished_at ?? row.started_at,
        started_at: row.started_at,
        finished_at: row.finished_at ?? null,
      });
      if (result?.markSeen) markSeen(row.id);   // consumer acks; we only mark seen
    }

    // Fetch the inbox id + backlog, emit `connected` (loads the inbox chat into memory),
    // then replay backlog + any rows that raced ahead. Idempotent: seenIds + the delivered
    // gate dedup overlaps, so it is safe to call on the initial attach, every reconnect,
    // and on window focus.
    drain = async () => {
      const { proactive_chat_id, jobs } = await initProactive();
      proactiveChatId = proactive_chat_id;                       // unblocks handleRow
      await onEvent({ type: "connected", proactive_chat_id });   // preserves connected handler
      const queued = pending.splice(0);                          // flush rows that raced ahead
      for (const row of [...jobs, ...queued]) await handleRow(row); // seenIds dedups overlaps
    };

    channel = supabase
      .channel(`proactive:${uid}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "proactive_jobs", filter: `user_id=eq.${uid}` },
        (payload) => { void handleRow(payload.new as ProactiveJobRow); },
      )
      .subscribe(async (status) => {
        if (cancelled || status !== "SUBSCRIBED") return;            // also fires on reconnect
        try {
          await drain?.();
        } catch (err) {
          console.error("[proactive] init/drain failed:", err);
        }
      });

    checkinTimer = setInterval(() => { void initProactive().catch(() => {}); }, 6 * 60 * 60 * 1000);
    window.addEventListener("focus", onFocus);
  }

  void connect();

  return () => {
    cancelled = true;
    if (retryTimer) clearTimeout(retryTimer);
    if (checkinTimer) clearInterval(checkinTimer);
    window.removeEventListener("focus", onFocus);
    if (channel) void supabase.removeChannel(channel);
  };
}

export async function ackProactiveJob(id: string): Promise<boolean> {
  try {
    const res = await authenticatedFetch(`${await getBase()}/agent/proactive/ack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    if (!res.ok) throw new Error(String(res.status));
    return true;
  } catch (err) {
    console.warn("[proactive_stream] proactive ack failed:", err);
    return false;
  }
}

export interface BehaviorPattern {
  id: string;
  pattern_type: string;
  query_template: string;
  frequency: number;
  auto_run: boolean;
  confidence: number;
  last_seen_at: string;
  first_seen_at: string;
}

export async function getPatterns(): Promise<BehaviorPattern[]> {
  const res = await authenticatedFetch(`${await getBase()}/debug/patterns`);
  if (!res.ok) throw new Error(`getPatterns failed: ${res.status}`);
  const data = (await res.json()) as { patterns: BehaviorPattern[] };
  return data.patterns ?? [];
}

export function getOrCreateSessionId(): string {
  let id = localStorage.getItem("recall_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("recall_session_id", id);
  }
  return id;
}

// ── Hotkey (speak-mode) chat persistence ─────────────────────────────────────
// The orb window is the sole writer of the single dedicated "Hotkey" chat. It
// resolves the user from its own auth session (the same one that authenticates
// every capture), so no AgentChatsProvider / userId prop is needed in that window.

const HOTKEY_CHAT_ID_KEY = "recall_hotkey_chat_id";

async function sessionUserId(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  return data.session?.user?.id ?? null;
}

/**
 * Find or create the user's single hotkey chat and return it. Returns null when
 * logged out (in which case no capture happens either, so there is nothing to persist).
 */
export async function ensureHotkeyChat(sessionId: string): Promise<AgentChat | null> {
  const userId = await sessionUserId();
  if (!userId) return null;

  const { data, error } = await supabase
    .from("agent_chats")
    .select("*")
    .eq("user_id", userId)
    .eq("is_hotkey", true)
    .limit(1);
  if (!error && data && data.length > 0) {
    const chat = normalizeStoredAgentChat(data[0] as Record<string, unknown>);
    try { localStorage.setItem(HOTKEY_CHAT_ID_KEY, chat.id); } catch { /* ignore */ }
    return chat;
  }

  let cachedId: string | null = null;
  try { cachedId = localStorage.getItem(HOTKEY_CHAT_ID_KEY); } catch { /* ignore */ }
  const ts = new Date().toISOString();
  const chat: AgentChat = {
    id: cachedId ?? crypto.randomUUID(),
    user_id: userId,
    agent_session_id: sessionId,
    title: "Hotkey",
    turns: [],
    last_capture: null,
    archived_at: null,
    created_at: ts,
    updated_at: ts,
    is_hotkey: true,
  };
  const { error: insErr } = await supabase.from("agent_chats").upsert(
    {
      id: chat.id,
      user_id: chat.user_id,
      agent_session_id: chat.agent_session_id,
      title: chat.title,
      turns: chat.turns,
      is_hotkey: true,
      created_at: ts,
      updated_at: ts,
    },
    { onConflict: "id" },
  );
  if (insErr) {
    console.warn("[hotkey] ensure chat failed:", insErr.message);
    return null;
  }
  try { localStorage.setItem(HOTKEY_CHAT_ID_KEY, chat.id); } catch { /* ignore */ }
  return chat;
}

/** Upsert the hotkey chat's turns. `is_hotkey` is set explicitly so it survives. */
export async function persistHotkeyChat(
  chat: Pick<AgentChat, "id" | "user_id" | "agent_session_id" | "turns">,
): Promise<void> {
  const { error } = await supabase.from("agent_chats").upsert(
    {
      id: chat.id,
      user_id: chat.user_id,
      agent_session_id: chat.agent_session_id,
      title: "Hotkey",
      turns: chat.turns,
      is_hotkey: true,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "id" },
  );
  if (error) console.warn("[hotkey] persist failed:", error.message);
}

export async function storeGoogleTokens(input: {
  provider_token: string | null;
  provider_refresh_token: string;
  google_token_expiry: string | null;
}): Promise<void> {
  const res = await authenticatedFetch(`${await getBase()}/auth/google/tokens`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => String(res.status));
    throw new Error(`Google token sync failed: ${detail}`);
  }
}


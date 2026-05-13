import { getAuthHeader } from "../hooks/useAuth";
import { getBase } from "./backend";

export type StreamEvent =
  | { type: "transcript"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_call"; name: string; input: unknown }
  | { type: "tool_result"; name: string; summary: string; data: Record<string, unknown> }
  | { type: "ack_audio"; audio_base64: string; text: string }
  | { type: "spoken"; text: string }
  | { type: "metadata"; intent_type: string; should_store: boolean; due_hint: string | null; reminder_text: string | null; awaiting_clarification: boolean }
  | { type: "stored"; item_id: string | null; due_at: string | null }
  | { type: "audio"; audio_base64: string }
  | { type: "error"; message: string; }
  | { type: "done" };

async function* _streamEvents(form: FormData): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${await getBase()}/capture/stream`, {
    method: "POST",
    body: form,
    headers: await getAuthHeader(),
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => String(res.status));
    throw new Error(`capture/stream failed: ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
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
): AsyncGenerator<StreamEvent> {
  const form = new FormData();
  form.append("audio", audioBlob, "recording.webm");
  form.append("session_id", sessionId);
  form.append("timezone", Intl.DateTimeFormat().resolvedOptions().timeZone);
  yield* _streamEvents(form);
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

export interface RecallItem {
  id: string;
  content: string;
  intent_type: string;
  status: string;
  created_at: string;
  updated_at: string;
  due_hint: string | null;
  due_at?: string | null;
  display_text?: string;
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
  const res = await fetch(`${await getBase()}/capture/suggest-title`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await getAuthHeader()) },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) return null;
  const data = (await res.json()) as { title?: string | null };
  const t = typeof data.title === "string" ? data.title.trim() : "";
  return t.length > 0 ? t : null;
}

export async function queryText(text: string, sessionId: string) {
  const res = await fetch(`${await getBase()}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await getAuthHeader()) },
    body: JSON.stringify({ text, session_id: sessionId }),
  });
  if (!res.ok) throw new Error(`query failed: ${res.status}`);
  return res.json();
}

export async function getItems(params?: {
  status?: string;
  has_due_hint?: boolean;
  limit?: number;
}): Promise<RecallItem[]> {
  const url = new URL(`${await getBase()}/items`);
  if (params?.status) url.searchParams.set("status", params.status);
  if (params?.has_due_hint) url.searchParams.set("has_due_hint", "true");
  if (params?.limit) url.searchParams.set("limit", String(params.limit));
  const res = await fetch(url.toString(), { headers: await getAuthHeader() });
  if (!res.ok) throw new Error(`getItems failed: ${res.status}`);
  return res.json();
}

export async function updateItem(
  id: string,
  update: { status?: string; due_hint?: string },
): Promise<RecallItem> {
  const res = await fetch(`${await getBase()}/items/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(await getAuthHeader()) },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error(`updateItem failed: ${res.status}`);
  return res.json();
}

let _audioQueue: Promise<void> = Promise.resolve();

export function playAudio(base64mp3: string, onEnd?: () => void): void {
  _audioQueue = _audioQueue.then(
    () =>
      new Promise<void>((resolve) => {
        const audio = new Audio(`data:audio/mpeg;base64,${base64mp3}`);
        const finish = () => {
          onEnd?.();
          resolve();
        };
        audio.addEventListener("ended", finish);
        audio.addEventListener("error", (e) => {
          console.error("Audio playback error:", e);
          finish();
        });
        audio.play().catch((err) => {
          console.error("Audio play() rejected:", err);
          finish();
        });
      }),
  );
}

export interface DueReminder {
  id: string;
  content: string;
  intent_type: string;
  audio_base64: string;
}

export async function getPendingReminders(): Promise<PendingReminder[]> {
  const res = await fetch(`${await getBase()}/reminders/pending`, { headers: await getAuthHeader() });
  if (!res.ok) throw new Error(`reminders/pending failed: ${res.status}`);
  return res.json();
}

export async function checkDueReminders(): Promise<DueReminder[]> {
  const res = await fetch(`${await getBase()}/reminders/due`, { headers: await getAuthHeader() });
  if (!res.ok) throw new Error(`reminders/due failed: ${res.status}`);
  return res.json();
}

export async function dismissReminders(ids: string[]): Promise<void> {
  if (ids.length === 0) return;
  const res = await fetch(`${await getBase()}/reminders/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await getAuthHeader()) },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) throw new Error(`reminders/dismiss failed: ${res.status}`);
}

export async function markRemindersAsMissed(): Promise<{ id: string; content: string }[]> {
  const res = await fetch(`${await getBase()}/reminders/mark-missed`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await getAuthHeader()) },
  });
  if (!res.ok) throw new Error(`reminders/mark-missed failed: ${res.status}`);
  const data: { items: { id: string; content: string }[] } = await res.json();
  return data.items;
}

export function getOrCreateSessionId(): string {
  let id = localStorage.getItem("recall_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("recall_session_id", id);
  }
  return id;
}


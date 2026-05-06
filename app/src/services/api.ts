import { getAuthHeader } from "../hooks/useAuth";
import { getBase } from "./backend";

export type StreamEvent =
  | { type: "transcript"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_call"; name: string; input: unknown }
  | { type: "tool_result"; name: string; summary: string; data: Record<string, unknown> }
  | { type: "ack_audio"; audio_base64: string; text: string }
  | { type: "spoken"; text: string }
  | { type: "metadata"; intent_type: string; should_store: boolean; due_hint: string | null; reminder_text: string | null }
  | { type: "stored"; item_id: string | null; due_at: string | null }
  | { type: "audio"; audio_base64: string }
  | { type: "error"; message: string }
  | { type: "done" };

async function* _streamEvents(form: FormData): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${await getBase()}/capture/stream`, {
    method: "POST",
    body: form,
    headers: getAuthHeader(),
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
  yield* _streamEvents(form);
}

export async function* captureStreamText(
  text: string,
  sessionId: string,
): AsyncGenerator<StreamEvent> {
  const form = new FormData();
  form.append("text", text);
  form.append("session_id", sessionId);
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
}

export interface CaptureResponse {
  transcript: string;
  response_text: string;
  audio_base64: string;
  intent_type: string;
  item_id: string | null;
  due_at: string | null;
}

export interface PendingReminder {
  id: string;
  content: string;
  intent_type: string;
  due_at: string;
}

export async function capture(
  audioBlob: Blob,
  sessionId: string,
): Promise<CaptureResponse> {
  const form = new FormData();
  form.append("audio", audioBlob, "recording.webm");
  form.append("session_id", sessionId);
  const res = await fetch(`${await getBase()}/capture`, {
    method: "POST",
    body: form,
    headers: getAuthHeader(),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => String(res.status));
    throw new Error(`capture failed: ${detail}`);
  }
  return res.json();
}

export async function queryText(text: string, sessionId: string) {
  const res = await fetch(`${await getBase()}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeader() },
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
  const res = await fetch(url.toString(), { headers: getAuthHeader() });
  if (!res.ok) throw new Error(`getItems failed: ${res.status}`);
  return res.json();
}

export async function updateItem(
  id: string,
  update: { status?: string; due_hint?: string },
): Promise<RecallItem> {
  const res = await fetch(`${await getBase()}/items/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...getAuthHeader() },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error(`updateItem failed: ${res.status}`);
  return res.json();
}

export function playAudio(base64mp3: string, onEnd?: () => void): void {
  const audio = new Audio(`data:audio/mpeg;base64,${base64mp3}`);
  if (onEnd) audio.addEventListener("ended", onEnd);
  audio.play().catch(console.error);
}

export interface DueReminder {
  id: string;
  content: string;
  intent_type: string;
  audio_base64: string;
}

export async function getPendingReminders(): Promise<PendingReminder[]> {
  const res = await fetch(`${await getBase()}/reminders/pending`, { headers: getAuthHeader() });
  if (!res.ok) throw new Error(`reminders/pending failed: ${res.status}`);
  return res.json();
}

export async function checkDueReminders(): Promise<DueReminder[]> {
  const res = await fetch(`${await getBase()}/reminders/due`, { headers: getAuthHeader() });
  if (!res.ok) throw new Error(`reminders/due failed: ${res.status}`);
  return res.json();
}

export async function dismissReminders(ids: string[]): Promise<void> {
  if (ids.length === 0) return;
  await fetch(`${await getBase()}/reminders/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeader() },
    body: JSON.stringify({ ids }),
  });
}

export function getOrCreateSessionId(): string {
  let id = localStorage.getItem("recall_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("recall_session_id", id);
  }
  return id;
}

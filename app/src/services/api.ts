const BASE = "http://localhost:8000";

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
}

export async function capture(
  audioBlob: Blob,
  sessionId: string,
): Promise<CaptureResponse> {
  const form = new FormData();
  form.append("audio", audioBlob, "recording.webm");
  form.append("session_id", sessionId);
  const res = await fetch(`${BASE}/capture`, { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.text().catch(() => String(res.status));
    throw new Error(`capture failed: ${detail}`);
  }
  return res.json();
}

export async function queryText(text: string, sessionId: string) {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  const url = new URL(`${BASE}/items`);
  if (params?.status) url.searchParams.set("status", params.status);
  if (params?.has_due_hint) url.searchParams.set("has_due_hint", "true");
  if (params?.limit) url.searchParams.set("limit", String(params.limit));
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`getItems failed: ${res.status}`);
  return res.json();
}

export async function updateItem(
  id: string,
  update: { status?: string; due_hint?: string },
): Promise<RecallItem> {
  const res = await fetch(`${BASE}/items/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
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

export function getOrCreateSessionId(): string {
  let id = localStorage.getItem("recall_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("recall_session_id", id);
  }
  return id;
}

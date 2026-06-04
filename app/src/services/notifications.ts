import { useSyncExternalStore } from "react";

// Persistent in-app log of proactive/reminder alerts (spoken or carded). The card
// and the orb are transient; this is the durable feed behind the titlebar bell.
const STORAGE_KEY = "recall_notifications";
const CAP = 50;

export interface NotificationEntry {
  id: string; // dedupe key — proactive job id, or `reminder:<itemId>:<firedMs>`
  kind: string; // proactive job_type, or "reminder"
  message: string;
  ts: string; // ISO
  read: boolean;
}

let entries: NotificationEntry[] = load();
const listeners = new Set<() => void>();

function load(): NotificationEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as NotificationEntry[]) : [];
  } catch {
    return [];
  }
}

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    /* quota — ignore */
  }
}

function emitChange(): void {
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function getSnapshot(): NotificationEntry[] {
  return entries;
}

/** Append an alert. Deduped by `id` so reconnect re-delivery never double-logs. */
export function addNotification(input: { id: string; kind: string; message: string; ts?: string }): void {
  if (entries.some((e) => e.id === input.id)) return;
  const entry: NotificationEntry = {
    id: input.id,
    kind: input.kind,
    message: input.message,
    ts: input.ts ?? new Date().toISOString(),
    read: false,
  };
  entries = [entry, ...entries].slice(0, CAP);
  persist();
  emitChange();
}

export function markAllNotificationsRead(): void {
  if (entries.every((e) => e.read)) return;
  entries = entries.map((e) => (e.read ? e : { ...e, read: true }));
  persist();
  emitChange();
}

export function clearNotifications(): void {
  if (entries.length === 0) return;
  entries = [];
  persist();
  emitChange();
}

export function markNotificationRead(id: string): void {
  let changed = false;
  entries = entries.map((e) => {
    if (e.id === id && !e.read) {
      changed = true;
      return { ...e, read: true };
    }
    return e;
  });
  if (changed) {
    persist();
    emitChange();
  }
}

/** Category label for a proactive delivery, keyed off the real registry job_type. */
export function proactiveLabel(jobType: string, result?: { email_cards?: unknown[] }): string {
  switch (jobType) {
    case "morning_brief":
      return "Morning brief is ready";
    case "email_triage": {
      const n = result?.email_cards?.length ?? 0;
      return n > 0 ? `${n} email${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} a reply` : "Emails need attention";
    }
    case "follow_up_scan":
      return "Follow-ups to review";
    case "follow_up_draft":
      return "A follow-up draft is ready";
    case "pattern_learn":
      return "Patterns updated";
    default:
      return "New update from Recall";
  }
}

export function useNotifications(): { entries: NotificationEntry[]; unread: number } {
  const list = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const unread = list.reduce((n, e) => n + (e.read ? 0 : 1), 0);
  return { entries: list, unread };
}

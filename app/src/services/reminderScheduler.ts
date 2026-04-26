import { emit } from "@tauri-apps/api/event";
import { checkDueReminders, getPendingReminders } from "./api";

const MISSED_THRESHOLD_MS = 60 * 60 * 1000; // reminders older than 1h on startup = missed
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 5_000;

// itemId → timeout handle; lets us cancel before firing and re-arm idempotently
const activeTimers = new Map<string, ReturnType<typeof setTimeout>>();

// Guards against two concurrent fireDue() calls (e.g. timer + focus event racing)
let firing = false;

const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, ms));

async function fireDue(): Promise<void> {
  if (firing) return;
  firing = true;

  let lastErr: unknown;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const reminders = await checkDueReminders();
      for (const r of reminders) {
        await emit("recall:reminder", { audio_base64: r.audio_base64, content: r.content });
        await emit("recall:new-turn", {
          transcript: `Reminder: ${r.content}`,
          response_text: `Reminder: ${r.content}`,
          intent_type: r.intent_type,
          item_id: r.id,
        });
      }
      firing = false;
      return;
    } catch (e) {
      lastErr = e;
      if (attempt < MAX_RETRIES - 1) await sleep(RETRY_DELAY_MS);
    }
  }

  firing = false;
  console.error("All reminder retries failed:", lastErr);
  await emit("recall:reminder-failed", {}).catch(() => {});
}

export function scheduleReminder(id: string, dueAt: string): void {
  // Clear any existing timer for this id (idempotent re-arm)
  const existing = activeTimers.get(id);
  if (existing !== undefined) clearTimeout(existing);

  const delayMs = new Date(dueAt).getTime() - Date.now();

  if (delayMs <= 0) {
    activeTimers.delete(id);
    fireDue();
  } else {
    const t = setTimeout(() => {
      activeTimers.delete(id);
      fireDue();
    }, delayMs);
    activeTimers.set(id, t);
  }
}

export function cancelReminder(id: string): void {
  const t = activeTimers.get(id);
  if (t !== undefined) {
    clearTimeout(t);
    activeTimers.delete(id);
  }
}

export async function loadPendingReminders(): Promise<void> {
  try {
    const items = await getPendingReminders();
    const now = Date.now();
    const missed: { id: string; content: string }[] = [];

    for (const item of items) {
      const age = now - new Date(item.due_at).getTime();
      if (age > MISSED_THRESHOLD_MS) {
        missed.push({ id: item.id, content: item.content });
      } else {
        scheduleReminder(item.id, item.due_at);
      }
    }

    if (missed.length > 0) {
      await emit("recall:reminders-missed", { items: missed }).catch(() => {});
    }
  } catch (e) {
    console.error("Failed to load pending reminders:", e);
  }
}

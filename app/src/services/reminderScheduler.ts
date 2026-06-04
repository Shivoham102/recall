import { emit } from "@tauri-apps/api/event";
import { checkDueReminders, getPendingReminders, markRemindersAsMissed } from "./api";
import { bumpSuppressed, isQuietContext } from "./quietContext";
import { showQuietCard } from "./notify";
import { addNotification } from "./notifications";

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 5_000;

// itemId → timeout handle; lets us cancel before firing and re-arm idempotently
const activeTimers = new Map<string, ReturnType<typeof setTimeout>>();

// Guards against two concurrent fireDue() calls (e.g. timer + focus event racing)
let firing = false;

// Guards against concurrent loadPendingReminders() calls; queues one follow-up if needed
let loadingPending = false;
let needsReload = false;

const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, ms));

async function fireDue(): Promise<void> {
  if (firing) return;
  firing = true;

  let lastErr: unknown;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      // Decide quiet BEFORE fetching: when carding we pass silent=1 so the backend
      // skips TTS synthesis for audio the card would discard.
      const quiet = await isQuietContext();
      const reminders = await checkDueReminders(quiet);
      for (const r of reminders) {
        if (quiet) {
          void showQuietCard("reminder", { content: r.content });
          bumpSuppressed();
        } else {
          await emit("recall:reminder", { audio_base64: r.audio_base64, content: r.content });
        }
        // Log to the notification center regardless of how it was delivered.
        addNotification({ id: `reminder:${r.id}:${Date.now()}`, kind: "reminder", message: `Reminder: ${r.content}` });
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
  if (loadingPending) { needsReload = true; return; }
  loadingPending = true;
  try {
    // Persist first: backend finds open items >2h past due, marks them missed
    const missed = await markRemindersAsMissed();
    if (missed.length > 0) {
      await emit("recall:reminders-missed", { items: missed }).catch(() => {});
    }
    // Schedule timers for remaining open/future items
    const items = await getPendingReminders();
    for (const item of items) {
      scheduleReminder(item.id, item.due_at);
    }
  } catch (e) {
    console.error("Failed to load pending reminders:", e);
    // No emit on failure — items stay open and retry on next focus/reopen
  } finally {
    loadingPending = false;
    if (needsReload) { needsReload = false; void loadPendingReminders(); }
  }
}

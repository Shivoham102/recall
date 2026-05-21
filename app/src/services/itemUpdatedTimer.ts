import { cancelReminder, scheduleReminder } from "./reminderScheduler";

const loggedInvalidItemUpdates = new Set<string>();

function logInvalidItemUpdate(reason: string, payload: unknown): void {
  if (loggedInvalidItemUpdates.has(reason)) return;
  loggedInvalidItemUpdates.add(reason);
  console.warn(`[item_updated] Ignoring invalid reminder update: ${reason}`, payload);
}

export function applyItemUpdatedTimer(
  itemId: unknown,
  dueAt: unknown,
  source: string,
): void {
  if (typeof itemId !== "string" || itemId.trim() === "") {
    logInvalidItemUpdate(`${source}:missing-item-id`, { itemId, dueAt });
    return;
  }

  if (dueAt === null) {
    cancelReminder(itemId);
    return;
  }

  if (typeof dueAt === "string" && Number.isFinite(new Date(dueAt).getTime())) {
    scheduleReminder(itemId, dueAt);
    return;
  }

  logInvalidItemUpdate(`${source}:invalid-due-at`, { itemId, dueAt });
}

import { emit, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import type { ProactiveJobResult } from "./api";

export type CardKind = "morning_brief" | "reminder";

interface ReminderLike {
  content: string;
}

// Fixed, category-based labels. Never derived from result.text (which is freeform
// and may contain anything unsuitable for a one-line card).
export function buildCardMessage(
  kind: CardKind,
  data: ProactiveJobResult | ReminderLike,
): string {
  if (kind === "reminder") {
    const content = (data as ReminderLike).content?.trim();
    return content ? `Reminder: ${content}` : "Reminder";
  }
  return "Morning brief is ready";
}

// Sequential queue: one card on screen at a time. The card window emits
// `recall:notif-dismissed` when it slides out; we then show the next queued card.
const queue: string[] = [];
let showing = false;
let dismissWired = false;

function wireDismiss(): void {
  if (dismissWired) return;
  dismissWired = true;
  void listen("recall:notif-dismissed", () => {
    showing = false;
    void pump();
  });
}

async function pump(): Promise<void> {
  if (showing) return;
  const message = queue.shift();
  if (message === undefined) return;
  showing = true;
  try {
    await invoke("show_notif");
    await emit("recall:notif-show", { message });
  } catch {
    showing = false; // failed to show — let a later call retry the queue
  }
}

export async function showQuietCard(
  kind: CardKind,
  data: ProactiveJobResult | ReminderLike,
): Promise<void> {
  wireDismiss();
  queue.push(buildCardMessage(kind, data));
  await pump();
}

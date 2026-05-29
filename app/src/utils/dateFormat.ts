export function formatDue(item: { due_at?: string | null; due_hint?: string | null }): string {
  if (item.due_at) {
    const dueDate = new Date(item.due_at);
    if (!Number.isNaN(dueDate.getTime())) {
      const now = new Date();
      const time = formatTime(dueDate);
      if (
        dueDate.getFullYear() === now.getFullYear() &&
        dueDate.getMonth() === now.getMonth() &&
        dueDate.getDate() === now.getDate()
      ) {
        return `TODAY AT ${time}`;
      }
      const monthDay = dueDate
        .toLocaleDateString([], { month: "short", day: "numeric" })
        .toUpperCase();
      return `${monthDay} AT ${time}`;
    }
  }
  return (item.due_hint ?? "").toUpperCase();
}

export function formatTime(date: Date): string {
  const options =
    date.getMinutes() === 0
      ? { hour: "numeric" as const }
      : { hour: "numeric" as const, minute: "2-digit" as const };
  return date.toLocaleTimeString([], options).toUpperCase();
}

const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; // 0=Mon..6=Sun

interface Recurrence {
  freq: "daily" | "weekdays" | "weekly";
  time: string; // "HH:MM"
  days?: number[];
  tz: string;
}

/** Human label for a recurring reminder, e.g. "Every day at 6:00 PM", "Weekdays at 9:00 AM". */
export function formatRecurrence(recurrence: Recurrence): string {
  const time = formatClockTime(recurrence.time);
  if (recurrence.freq === "daily") return `Every day at ${time}`;
  if (recurrence.freq === "weekdays") return `Weekdays at ${time}`;
  // weekly
  const days = (recurrence.days ?? []).slice().sort((a, b) => a - b);
  if (days.length === 0) return `Every day at ${time}`;
  const labels = days.map((d) => WEEKDAY_NAMES[d] ?? "?").join(", ");
  return `${labels} at ${time}`;
}

function formatClockTime(hhmm: string): string {
  const [h, m] = hhmm.split(":").map((n) => parseInt(n, 10));
  if (Number.isNaN(h)) return hhmm;
  const d = new Date();
  d.setHours(h, Number.isNaN(m) ? 0 : m, 0, 0);
  return formatTime(d).replace(/\b([AP]M)\b/i, (s) => s.toUpperCase());
}

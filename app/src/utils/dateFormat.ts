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

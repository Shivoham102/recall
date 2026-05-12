import { useCallback, useEffect, useState } from "react";
import { getItems, updateItem, RecallItem } from "../../services/api";

export function RemindersTab() {
  const [items, setItems] = useState<RecallItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getItems({ has_due_hint: true, status: "open" });
      setItems(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const dismiss = async (id: string) => {
    await updateItem(id, { status: "resolved" });
    setItems((prev) => prev.filter((i) => i.id !== id));
  };

  if (loading) return <div className="tab-loading">Loading…</div>;

  return (
    <div className="reminders-tab">
      <div className="tab-header">
        <span className="tab-header__title">Upcoming reminders</span>
        <span className="tab-header__count">{items.length}</span>
        <button className="tab-refresh" onClick={load}>↺</button>
      </div>

      {items.length === 0 && (
        <div className="tab-empty">No reminders set. Tell the agent to remind you of something.</div>
      )}

      <div className="reminder-list">
        {items.map((item) => (
          <div key={item.id} className="reminder-card">
            <div className="reminder-card__due">
              <span className="reminder-card__due-icon">◷</span>
              {formatReminderDue(item)}
            </div>
            <p className="reminder-card__content">{item.display_text || item.content}</p>
            <div className="reminder-card__footer">
              <span className="reminder-card__date">
                Added {new Date(item.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}
              </span>
              <button className="reminder-card__dismiss" onClick={() => dismiss(item.id)}>
                Dismiss
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatReminderDue(item: RecallItem): string {
  if (item.due_at) {
    const dueDate = new Date(item.due_at);
    if (!Number.isNaN(dueDate.getTime())) {
      const now = new Date();
      const formattedTime = formatTime(dueDate);
      if (
        dueDate.getFullYear() === now.getFullYear()
        && dueDate.getMonth() === now.getMonth()
        && dueDate.getDate() === now.getDate()
      ) {
        return `TODAY AT ${formattedTime}`;
      }
      const monthDay = dueDate.toLocaleDateString([], { month: "short", day: "numeric" }).toUpperCase();
      return `${monthDay} AT ${formattedTime}`;
    }
  }
  return (item.due_hint ?? "").toUpperCase();
}

function formatTime(date: Date): string {
  const options = date.getMinutes() === 0
    ? { hour: "numeric" as const }
    : { hour: "numeric" as const, minute: "2-digit" as const };
  return date.toLocaleTimeString([], options).toUpperCase();
}

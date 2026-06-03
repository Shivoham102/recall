import { useCallback, useEffect, useState } from "react";
import { getItems, updateItem, deleteItem, RecallItem } from "../../services/api";
import { TabLoading } from "../TabLoading";
import { useToast } from "../Toast";

const TYPE_ORDER = ["blocker", "task", "follow_up", "follow_up_draft", "progress", "note"];

export function TasksTab() {
  const [items, setItems] = useState<RecallItem[]>([]);
  const [loading, setLoading] = useState(true);
  const { scheduleUndo, isPending } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = (await getItems({ status: "open", has_due_hint: false })).filter((i) => !isPending(i.id));
      setItems(data.sort((a, b) => {
        const typeDiff = TYPE_ORDER.indexOf(a.intent_type) - TYPE_ORDER.indexOf(b.intent_type);
        if (typeDiff !== 0) return typeDiff;
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      }));
    } finally {
      setLoading(false);
    }
  }, [isPending]);

  useEffect(() => { void load(); }, [load]);

  const removeWithUndo = (item: RecallItem, message: string, commit: () => Promise<unknown>) => {
    const index = items.findIndex((i) => i.id === item.id);
    setItems((prev) => prev.filter((i) => i.id !== item.id));
    scheduleUndo({
      id: item.id,
      message,
      onUndo: () =>
        setItems((prev) => {
          if (prev.some((i) => i.id === item.id)) return prev;
          const next = [...prev];
          next.splice(Math.max(0, index), 0, item);
          return next;
        }),
      commit,
    });
  };

  const resolve = (item: RecallItem) => removeWithUndo(item, "Resolved", () => updateItem(item.id, { status: "resolved" }));
  const remove = (item: RecallItem) => removeWithUndo(item, "Deleted", () => deleteItem(item.id));

  if (loading) return <TabLoading />;

  return (
    <div className="tasks-tab">
      <div className="tab-header">
        <span className="tab-header__title">Open items</span>
        <span className="tab-header__count">{items.length}</span>
        <button className="tab-refresh" onClick={load}>↺</button>
      </div>

      {items.length === 0 && (
        <div className="tab-empty">Nothing open. Nice work.</div>
      )}

      <div className="task-list">
        {items.map((item) => (
          <div key={item.id} className="task-card">
            <span
              className={`task-card__badge badge-type badge-type--${item.intent_type}`}
            >
              {item.intent_type.replace("_", " ")}
            </span>
            <p className="task-card__content">{item.display_text || item.content}</p>
            <div className="task-card__footer">
              <span className="task-card__date">{fmt(item.updated_at)}</span>
              <div className="task-card__actions">
                <button className="task-card__delete" onClick={() => remove(item)} title="Delete (wrong capture)" aria-label="Delete">×</button>
                <button className="task-card__resolve" onClick={() => resolve(item)} title="Mark resolved" aria-label="Resolve">✓</button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function fmt(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diffDays === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

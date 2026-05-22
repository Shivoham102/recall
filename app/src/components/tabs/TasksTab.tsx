import { useCallback, useEffect, useState } from "react";
import { getItems, updateItem, RecallItem } from "../../services/api";
import { TabLoading } from "../TabLoading";
import { formatDue } from "../../utils/dateFormat";

const TYPE_ORDER = ["blocker", "task", "follow_up", "follow_up_draft", "progress", "note"];
const TYPE_COLOR: Record<string, string> = {
  blocker:        "#ff4466",
  task:           "#00e5ff",
  follow_up:      "#ff9900",
  follow_up_draft: "#e06c00",
  progress:       "#00ff88",
  note:           "#9988ff",
};

export function TasksTab() {
  const [items, setItems] = useState<RecallItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getItems({ status: "open" });
      setItems(data.sort((a, b) => {
        const typeDiff = TYPE_ORDER.indexOf(a.intent_type) - TYPE_ORDER.indexOf(b.intent_type);
        if (typeDiff !== 0) return typeDiff;
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const resolve = async (id: string) => {
    await updateItem(id, { status: "resolved" });
    setItems((prev) => prev.filter((i) => i.id !== id));
  };

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
              className="task-card__badge"
              style={{ color: TYPE_COLOR[item.intent_type] ?? "#aaa", borderColor: TYPE_COLOR[item.intent_type] ?? "#aaa" }}
            >
              {item.intent_type.replace("_", " ")}
            </span>
            <p className="task-card__content">{item.display_text || item.content}</p>
            <div className="task-card__footer">
              <span className="task-card__date">{fmt(item.updated_at)}</span>
              {(item.due_hint || item.due_at) && (
                <span className="task-card__due">due: {formatDue(item).toLowerCase()}</span>
              )}
              <button className="task-card__resolve" onClick={() => resolve(item.id)} title="Mark resolved">✓</button>
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

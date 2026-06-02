import { useCallback, useEffect, useState } from "react";
import { clearMemory, deleteMemoryItem, listMemoryItems, MemoryItem } from "../../services/api";
import { BrainIcon } from "../icons/BrainIcon";
import { TabLoading } from "../TabLoading";

export function MemoryTab() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [status, setStatus] = useState<string>("ok");
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listMemoryItems();
      setItems(res.items ?? []);
      setStatus(res.status ?? "ok");
      setMessage("");
    } catch {
      setItems([]);
      setMessage("Could not load memory right now.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const onDelete = async (id: string) => {
    if (!confirm("Forget this memory?")) return;
    setDeletingId(id);
    try {
      await deleteMemoryItem(id);
      setItems((prev) => prev.filter((m) => m.id !== id));
      setMessage("");
    } catch {
      setMessage("Could not forget that memory right now.");
    } finally {
      setDeletingId(null);
    }
  };

  const onClear = async () => {
    if (!confirm("Clear everything Recall remembers about you?")) return;
    setClearing(true);
    try {
      const result = await clearMemory();
      setMessage(result.ok ? "Memory cleared." : "Could not clear memory right now.");
      await load();
    } catch {
      setMessage("Could not clear memory right now.");
    } finally {
      setClearing(false);
    }
  };

  if (loading) return <TabLoading />;

  const hasMemory = items.length > 0;
  const showUnavailable = status === "unavailable" || status === "disabled";

  return (
    <div className="memory-tab">
      <div className="memory-header">
        <div>
          <div className="memory-title">
            <BrainIcon className="memory-title__icon" size={16} />
            Memory
          </div>
          <div className="memory-subtitle">
            Everything Recall remembers about you. Forget anything you don't want kept.
          </div>
        </div>
        <button className="tab-refresh" onClick={load}>↺</button>
      </div>

      {showUnavailable && (
        <div className="memory-message">
          Memory is temporarily unavailable. Recall will keep working normally.
        </div>
      )}

      {message && <div className="memory-message">{message}</div>}

      <div className="memory-body">
        {!hasMemory ? (
          <div className="memory-empty">
            <BrainIcon className="memory-empty__icon" size={32} />
            <p>Nothing here yet.</p>
            <span>
              Mention things like “I prefer short emails” or “My manager is Sarah”
              and Recall will remember them for you.
            </span>
          </div>
        ) : (
          <div className="memory-section">
            <div className="memory-section__label">Stored memories</div>
            <div className="memory-list">
              {items.map((item) => {
                const processing = item.status != null && item.status !== "done";
                return (
                  <div key={item.id} className="memory-item memory-item--row">
                    <span className="memory-item__text">
                      {item.text}
                      {processing && <span className="memory-item__tag">processing</span>}
                    </span>
                    <button
                      className="memory-item__delete"
                      onClick={() => onDelete(item.id)}
                      disabled={deletingId === item.id}
                      aria-label="Forget this memory"
                      title="Forget this memory"
                    >
                      {deletingId === item.id ? "…" : "×"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <div className="memory-footer">
        <span>New memories may take a moment to appear.</span>
        {hasMemory && (
          <button className="memory-clear" onClick={onClear} disabled={clearing}>
            {clearing ? "Clearing..." : "Clear all memory"}
          </button>
        )}
      </div>
    </div>
  );
}

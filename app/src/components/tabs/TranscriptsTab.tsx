import { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { getItems, RecallItem } from "../../services/api";

const TYPE_COLOR: Record<string, string> = {
  task:      "#00e5ff",
  blocker:   "#ff4466",
  follow_up: "#ff9900",
  progress:  "#00ff88",
  note:      "#9988ff",
  query:     "#666688",
  update:    "#ffcc00",
};

type Group = { label: string; items: RecallItem[] };

function groupByDay(items: RecallItem[]): Group[] {
  const map = new Map<string, RecallItem[]>();
  const now = new Date();

  for (const item of items) {
    const d = new Date(item.created_at);
    const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
    const key =
      diffDays === 0 ? "Today" :
      diffDays === 1 ? "Yesterday" :
      d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });

    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(item);
  }

  return Array.from(map.entries()).map(([label, items]) => ({ label, items }));
}

export function TranscriptsTab() {
  const [items, setItems] = useState<RecallItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getItems({ limit: 200 });
      const byId = new Map<string, RecallItem>();
      for (const item of data) {
        if (!byId.has(item.id)) byId.set(item.id, item);
      }
      setItems(Array.from(byId.values()));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const unlisten = listen("recall:new-turn", () => { void load(); });
    return () => { unlisten.then((f) => f()); };
  }, [load]);

  const copy = (text: string, id: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(id);
      setTimeout(() => setCopied(null), 1500);
    });
  };

  const filtered = search.trim()
    ? items.filter((i) => i.content.toLowerCase().includes(search.toLowerCase()))
    : items;

  const groups = groupByDay(filtered);

  if (loading) return <div className="tab-loading">Loading…</div>;

  return (
    <div className="transcripts-tab">
      {/* Search bar */}
      <div className="transcripts-search-row">
        <div className="transcripts-search">
          <span className="transcripts-search__icon">⌕</span>
          <input
            ref={searchRef}
            className="transcripts-search__input"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search transcripts…"
          />
          {search && (
            <button className="transcripts-search__clear" onClick={() => setSearch("")}>✕</button>
          )}
        </div>
        <button className="tab-refresh" onClick={load}>↺</button>
      </div>

      <div className="transcripts-list">
        {groups.length === 0 && (
          <div className="tab-empty">No transcripts yet. Start speaking.</div>
        )}

        {groups.map((group) => (
          <div key={group.label} className="transcript-group">
            <div className="transcript-group__label">{group.label}</div>

            {group.items.map((item) => (
              <div
                key={item.id}
                className="transcript-entry"
                onMouseEnter={(e) => e.currentTarget.classList.add("transcript-entry--hover")}
                onMouseLeave={(e) => e.currentTarget.classList.remove("transcript-entry--hover")}
              >
                <div className="transcript-entry__meta">
                  <span
                    className="transcript-entry__type"
                    style={{ color: TYPE_COLOR[item.intent_type] ?? "#888" }}
                  >
                    {item.intent_type.replace("_", " ")}
                  </span>
                  <span className="transcript-entry__time">
                    {new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>

                <p className="transcript-entry__text">{item.content}</p>

                <button
                  className={`transcript-entry__copy ${copied === item.id ? "transcript-entry__copy--done" : ""}`}
                  onClick={() => copy(item.content, item.id)}
                  title="Copy"
                >
                  {copied === item.id ? "✓" : "⎘"}
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

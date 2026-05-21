import { useCallback, useEffect, useState } from "react";
import { clearMemory, getMemoryProfile, MemoryProfile } from "../../services/api";
import { BrainIcon } from "../icons/BrainIcon";
import { TabLoading } from "../TabLoading";

function MemorySection({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div className="memory-section">
      <div className="memory-section__label">{title}</div>
      {items.length === 0 ? (
        <div className="memory-empty-line">{empty}</div>
      ) : (
        <div className="memory-list">
          {items.map((item, idx) => (
            <div key={`${title}-${idx}`} className="memory-item">{item}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function MemoryTab() {
  const [profile, setProfile] = useState<MemoryProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setProfile(await getMemoryProfile());
      setMessage("");
    } catch {
      setProfile(null);
      setMessage("Could not load memory right now.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

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

  const staticFacts = profile?.profile.static ?? [];
  const dynamicContext = profile?.profile.dynamic ?? [];
  const relevant = profile?.relevant_memories ?? [];
  const hasMemory = staticFacts.length + dynamicContext.length + relevant.length > 0;
  const showUnavailable = profile?.status === "unavailable" || profile?.status === "disabled";

  return (
    <div className="memory-tab">
      <div className="memory-header">
        <div>
          <div className="memory-title">
            <BrainIcon className="memory-title__icon" size={16} />
            Memory
          </div>
          <div className="memory-subtitle">
            Preferences, people, and context Recall learns from your conversations.
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
          <>
            <MemorySection
              title="About you"
              items={staticFacts}
              empty="Stable facts about you will appear here."
            />
            <MemorySection
              title="Recent context"
              items={dynamicContext}
              empty="Recent personal context will appear here."
            />
            <MemorySection
              title="Related memories"
              items={relevant}
              empty="Related memories will appear here."
            />
          </>
        )}
      </div>

      <div className="memory-footer">
        <span>{profile?.processing_hint ?? "New memories may take a moment to appear."}</span>
        {hasMemory && (
          <button className="memory-clear" onClick={onClear} disabled={clearing}>
            {clearing ? "Clearing..." : "Clear all memory"}
          </button>
        )}
      </div>
    </div>
  );
}

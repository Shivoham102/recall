import { useCallback, useEffect, useState } from "react";
import {
  AgentSuggestion,
  acceptSuggestion,
  dismissSuggestion,
  getSuggestions,
} from "../services/api";

interface Props {
  /** Called after a suggestion is accepted so the parent can refresh its lists. */
  onChange?: () => void;
}

export function SuggestionStrip({ onChange }: Props) {
  const [suggestions, setSuggestions] = useState<AgentSuggestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    getSuggestions()
      .then(setSuggestions)
      .catch(() => setSuggestions([]));
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = async (id: string, fn: (id: string) => Promise<void>, refresh: boolean) => {
    setBusy(id);
    try {
      await fn(id);
      setSuggestions((prev) => prev.filter((s) => s.id !== id));
      if (refresh) onChange?.();
    } catch {
      // leave it in place; user can retry
    } finally {
      setBusy(null);
    }
  };

  if (suggestions.length === 0) return null;

  return (
    <div className="suggestion-strip">
      {suggestions.map((s) => (
        <div key={s.id} className="suggestion-card">
          <span className="suggestion-card__icon">💡</span>
          <p className="suggestion-card__title">{s.title}</p>
          <div className="suggestion-card__actions">
            <button
              className="suggestion-card__accept"
              disabled={busy === s.id}
              onClick={() => act(s.id, acceptSuggestion, true)}
            >
              {s.kind === "recurring_reminder" ? "Make recurring" : "Add reminder"}
            </button>
            <button
              className="suggestion-card__dismiss"
              disabled={busy === s.id}
              onClick={() => act(s.id, dismissSuggestion, false)}
            >
              Dismiss
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

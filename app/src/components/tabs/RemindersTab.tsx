import { Dispatch, SetStateAction, useCallback, useEffect, useState } from "react";
import { getItems, updateItem, deleteItem, RecallItem } from "../../services/api";
import { TabLoading } from "../TabLoading";
import { SuggestionStrip } from "../SuggestionStrip";
import { useToast } from "../Toast";
import { formatDue, formatRecurrence } from "../../utils/dateFormat";

// Snooze shifts the EXISTING due time by a fixed delta (not "from now"), so "+1h" on a
// reminder set for 6:24 makes it 7:24. "Tomorrow" is +24h from the set time.
const SNOOZE_PRESETS: { label: string; ms: number }[] = [
  { label: "+1h", ms: 60 * 60 * 1000 },
  { label: "+3h", ms: 3 * 60 * 60 * 1000 },
  { label: "Tomorrow", ms: 24 * 60 * 60 * 1000 },
];

export function RemindersTab() {
  const [items, setItems] = useState<RecallItem[]>([]);
  const [missed, setMissed] = useState<RecallItem[]>([]);
  const [loading, setLoading] = useState(true);
  const { scheduleUndo, isPending } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [open, missedData] = await Promise.all([
        getItems({ has_due_hint: true, status: "open" }),
        getItems({ has_due_hint: true, status: "missed" }),
      ]);
      setItems(open.filter((i) => !isPending(i.id)));
      setMissed(missedData.filter((i) => !isPending(i.id)));
    } finally {
      setLoading(false);
    }
  }, [isPending]);

  useEffect(() => { void load(); }, [load]);

  const removeWithUndo = (
    list: RecallItem[],
    setList: Dispatch<SetStateAction<RecallItem[]>>,
    item: RecallItem,
    message: string,
    commit: () => Promise<unknown>,
  ) => {
    const index = list.findIndex((i) => i.id === item.id);
    setList((prev) => prev.filter((i) => i.id !== item.id));
    scheduleUndo({
      id: item.id,
      message,
      onUndo: () =>
        setList((prev) => {
          if (prev.some((i) => i.id === item.id)) return prev;
          const next = [...prev];
          next.splice(Math.max(0, index), 0, item);
          return next;
        }),
      commit,
    });
  };

  const dismiss = (item: RecallItem) =>
    removeWithUndo(items, setItems, item, "Dismissed", () => updateItem(item.id, { status: "resolved" }));
  const stopRepeating = (item: RecallItem) =>
    removeWithUndo(items, setItems, item, "Stopped repeating", () => updateItem(item.id, { clear_recurrence: true }));
  const acknowledge = (item: RecallItem) =>
    removeWithUndo(missed, setMissed, item, "Acknowledged", () => updateItem(item.id, { status: "resolved" }));
  const done = (item: RecallItem) =>
    removeWithUndo(items, setItems, item, "Done", () => updateItem(item.id, { status: "resolved" }));
  const remove = (item: RecallItem, list: RecallItem[], setList: Dispatch<SetStateAction<RecallItem[]>>) =>
    removeWithUndo(list, setList, item, "Deleted", () => deleteItem(item.id));

  const snooze = async (item: RecallItem, ms: number, fromMissed = false) => {
    // Shift from the existing due time; for an already-past (missed) reminder, shift from now
    // so it lands in the future instead of staying in the past.
    const base = Math.max(item.due_at ? Date.parse(item.due_at) : 0, Date.now());
    const newDue = new Date(base + ms).toISOString();
    try {
      const updated = await updateItem(item.id, { due_at: newDue });
      if (fromMissed) {
        setMissed((prev) => prev.filter((i) => i.id !== item.id));
        setItems((prev) => [{ ...item, ...updated }, ...prev]);
      } else {
        setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, ...updated } : i)));
      }
    } catch {
      void load(); // server rejected — restore truth
    }
  };

  if (loading) return <TabLoading />;

  const recurring = items.filter((i) => i.recurrence);
  const upcoming = items.filter((i) => !i.recurrence && !i.reminded_at);
  const fired = items.filter((i) => !i.recurrence && i.reminded_at); // rang, awaiting action

  return (
    <div className="reminders-tab">
      <SuggestionStrip onChange={load} />

      {recurring.length > 0 && (
        <>
          <div className="tab-header">
            <span className="tab-header__title">Repeating</span>
            <span className="tab-header__count">{recurring.length}</span>
          </div>
          <div className="reminder-list">
            {recurring.map((item) => (
              <div key={item.id} className="reminder-card reminder-card--repeating">
                <div className="reminder-card__due">
                  <span className="reminder-card__due-icon">↻</span>
                  {item.recurrence ? formatRecurrence(item.recurrence) : formatDue(item)}
                </div>
                <p className="reminder-card__content">{item.display_text || item.content}</p>
                <div className="reminder-card__footer">
                  <span className="reminder-card__date">
                    Added {new Date(item.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}
                  </span>
                  <div className="reminder-card__actions">
                    <button className="reminder-card__dismiss" onClick={() => stopRepeating(item)}>Stop</button>
                    <button className="reminder-card__delete" onClick={() => remove(item, items, setItems)} title="Delete" aria-label="Delete">×</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="tab-header">
        <span className="tab-header__title">Upcoming reminders</span>
        <span className="tab-header__count">{upcoming.length}</span>
        <button className="tab-refresh" onClick={load}>↺</button>
      </div>

      {upcoming.length === 0 && recurring.length === 0 && fired.length === 0 && missed.length === 0 && (
        <div className="tab-empty">No reminders set. Tell the agent to remind you of something.</div>
      )}

      <div className="reminder-list">
        {upcoming.map((item) => (
          <div key={item.id} className="reminder-card">
            <div className="reminder-card__due">
              <span className="reminder-card__due-icon">◷</span>
              {formatDue(item)}
            </div>
            <p className="reminder-card__content">{item.display_text || item.content}</p>
            <div className="reminder-card__snooze">
              {SNOOZE_PRESETS.map((p) => (
                <button key={p.label} className="reminder-card__snooze-btn" onClick={() => snooze(item, p.ms)}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="reminder-card__footer">
              <span className="reminder-card__date">
                Added {new Date(item.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}
              </span>
              <div className="reminder-card__actions">
                <button className="reminder-card__dismiss" onClick={() => dismiss(item)}>Dismiss</button>
                <button className="reminder-card__delete" onClick={() => remove(item, items, setItems)} title="Delete" aria-label="Delete">×</button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {fired.length > 0 && (
        <>
          <div className="tab-header tab-header--reminded">
            <span className="tab-header__title">Reminded</span>
            <span className="tab-header__count">{fired.length}</span>
          </div>
          <div className="reminder-list">
            {fired.map((item) => (
              <div key={item.id} className="reminder-card reminder-card--fired">
                <div className="reminder-card__due">
                  <span className="reminder-card__due-icon">✓</span>
                  Reminded · {formatDue(item)}
                </div>
                <p className="reminder-card__content">{item.display_text || item.content}</p>
                <div className="reminder-card__snooze">
                  {SNOOZE_PRESETS.map((p) => (
                    <button key={p.label} className="reminder-card__snooze-btn" onClick={() => snooze(item, p.ms)}>
                      {p.label}
                    </button>
                  ))}
                </div>
                <div className="reminder-card__footer">
                  <span className="reminder-card__date">
                    Added {new Date(item.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}
                  </span>
                  <div className="reminder-card__actions">
                    <button className="reminder-card__dismiss" onClick={() => done(item)}>Done</button>
                    <button className="reminder-card__delete" onClick={() => remove(item, items, setItems)} title="Delete" aria-label="Delete">×</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {missed.length > 0 && (
        <>
          <div className="tab-header tab-header--missed">
            <span className="tab-header__title">Missed</span>
            <span className="tab-header__count">{missed.length}</span>
          </div>
          <div className="reminder-list reminder-list--missed">
            {missed.map((item) => (
              <div key={item.id} className="reminder-card reminder-card--missed">
                <div className="reminder-card__due">
                  <span className="reminder-card__due-icon">◷</span>
                  {formatDue(item)}
                </div>
                <p className="reminder-card__content">{item.display_text || item.content}</p>
                <div className="reminder-card__snooze">
                  {SNOOZE_PRESETS.map((p) => (
                    <button key={p.label} className="reminder-card__snooze-btn" onClick={() => snooze(item, p.ms, true)}>
                      {p.label}
                    </button>
                  ))}
                </div>
                <div className="reminder-card__footer">
                  <span className="reminder-card__date">
                    Added {new Date(item.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}
                  </span>
                  <div className="reminder-card__actions">
                    <button className="reminder-card__dismiss" onClick={() => acknowledge(item)}>Acknowledge</button>
                    <button className="reminder-card__delete" onClick={() => remove(item, missed, setMissed)} title="Delete" aria-label="Delete">×</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

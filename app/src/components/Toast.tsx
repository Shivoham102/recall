import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";

interface UndoOptions {
  /** The affected item id, tracked so tabs can suppress it on refetch until commit. */
  id: string;
  /** Short past-tense label, e.g. "Forgotten", "Dismissed". */
  message: string;
  /** Restore local state when the user clicks Undo. */
  onUndo: () => void;
  /** The real API call, fired only after the window elapses without an undo. */
  commit: () => Promise<unknown> | unknown;
  /** Window in ms before commit. Default 5000. */
  duration?: number;
}

interface ToastEntry extends UndoOptions {
  key: number;
  timer: ReturnType<typeof setTimeout>;
}

interface ToastContextValue {
  scheduleUndo: (opts: UndoOptions) => void;
  /** Live check (ref-backed, no stale closure) so load() can filter pending-removed rows. */
  isPending: (id: string) => boolean;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState<{ key: number; message: string }[]>([]);
  const entries = useRef<Map<number, ToastEntry>>(new Map());
  const pendingIds = useRef<Set<string>>(new Set());
  const keyCounter = useRef(0);

  const finalize = useCallback((key: number, run: "commit" | "undo") => {
    const entry = entries.current.get(key);
    if (!entry) return;
    clearTimeout(entry.timer);
    entries.current.delete(key);
    pendingIds.current.delete(entry.id);
    setVisible((v) => v.filter((t) => t.key !== key));
    try {
      if (run === "commit") void Promise.resolve(entry.commit()).catch(() => {});
      else entry.onUndo();
    } catch {
      /* ignore */
    }
  }, []);

  const scheduleUndo = useCallback((opts: UndoOptions) => {
    const key = ++keyCounter.current;
    const timer = setTimeout(() => finalize(key, "commit"), opts.duration ?? 5000);
    entries.current.set(key, { ...opts, key, timer });
    pendingIds.current.add(opts.id);
    setVisible((v) => [...v, { key, message: opts.message }]);
  }, [finalize]);

  const isPending = useCallback((id: string) => pendingIds.current.has(id), []);

  // Best-effort flush of pending commits when the app closes / provider unmounts. An abrupt
  // kill mid-window fails safe: the destructive action simply never happened (the row stays).
  useEffect(() => {
    const flush = () => {
      entries.current.forEach((e) => {
        clearTimeout(e.timer);
        try { void Promise.resolve(e.commit()).catch(() => {}); } catch { /* ignore */ }
      });
      entries.current.clear();
      pendingIds.current.clear();
    };
    window.addEventListener("beforeunload", flush);
    return () => {
      window.removeEventListener("beforeunload", flush);
      flush();
    };
  }, []);

  return (
    <ToastContext.Provider value={{ scheduleUndo, isPending }}>
      {children}
      {visible.length > 0 && (
        <div className="toast-stack">
          {visible.map((t) => (
            <div key={t.key} className="toast">
              <span className="toast__msg">{t.message}</span>
              <button className="toast__undo" onClick={() => finalize(t.key, "undo")}>Undo</button>
            </div>
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}

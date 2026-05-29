import { useCallback, useEffect, useSyncExternalStore, type ReactNode } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "recall_theme";
const DEFAULT_THEME: Theme = "dark";

type Listener = () => void;
const listeners = new Set<Listener>();

let theme: Theme = DEFAULT_THEME;

function emitChange() {
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot(): Theme {
  return theme;
}

export function readStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(next: Theme) {
  try {
    document.documentElement.dataset.theme = next;
  } catch {
    /* ignore */
  }
}

function persistTheme(next: Theme) {
  theme = next;
  try { localStorage.setItem(STORAGE_KEY, next); } catch { /* ignore */ }
  applyTheme(next);
  emitChange();
}

/**
 * Reads the persisted theme and applies it to <html> synchronously.
 * Call once at module load (before React renders) to avoid a flash of the
 * wrong theme. Safe to call from any window (orb + main).
 */
export function initTheme(): Theme {
  theme = readStoredTheme();
  applyTheme(theme);
  return theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Cross-tab sync (storage events do not fire in the same tab).
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      if (e.newValue === "light" || e.newValue === "dark") {
        persistTheme(e.newValue);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return children;
}

export function useTheme() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const setTheme = useCallback((next: Theme) => {
    if (next === getSnapshot()) return;
    persistTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    const current = getSnapshot();
    persistTheme(current === "dark" ? "light" : "dark");
  }, []);

  return { theme: current, setTheme, toggleTheme };
}

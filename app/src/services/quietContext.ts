import { invoke } from "@tauri-apps/api/core";

// Quiet context = the user is unavailable for voice (on a call / in a meeting).
// Mic-in-use is the signal — true even when muted in a call, since software mute
// keeps the OS capture stream open. A failed check resolves to `false` so the
// assistant speaks rather than going silent.
export async function isQuietContext(): Promise<boolean> {
  try {
    return await invoke<boolean>("is_mic_in_use");
  } catch {
    return false;
  }
}

// Count of alerts suppressed (carded instead of spoken) during the current quiet
// window. Module scope is safe: the watcher and both gate sites all run in the
// main window's single JS realm.
let suppressed = 0;
export function bumpSuppressed(): void {
  suppressed += 1;
}
export function peekSuppressed(): number {
  return suppressed;
}
export function drainSuppressed(): number {
  const n = suppressed;
  suppressed = 0;
  return n;
}

const POLL_MS = 3000;
const GRACE_MS = 5000;

// Polls quiet state; when it clears (quiet → free) and stays free through a 5s
// grace window, calls `onClear(n)` with the number of suppressed alerts (if > 0).
// Returns a stop function. Mount once from the main window.
export function startQuietWatcher(onClear: (n: number) => void): () => void {
  let stopped = false;
  let wasQuiet = false;
  let graceTimer: number | null = null;

  const clearGrace = () => {
    if (graceTimer !== null) {
      window.clearTimeout(graceTimer);
      graceTimer = null;
    }
  };

  const tick = async () => {
    if (stopped) return;
    // Nothing suppressed → nothing to nudge about. Skip entirely so we don't poll
    // the calendar (or mic) while idle; reset transition state.
    if (peekSuppressed() === 0) {
      wasQuiet = false;
      clearGrace();
      return;
    }
    const quiet = await isQuietContext();
    if (quiet) {
      wasQuiet = true;
      clearGrace(); // back in quiet before grace elapsed — cancel the pending nudge
    } else if (wasQuiet && graceTimer === null) {
      // Just transitioned quiet → free; confirm still free after the grace window.
      graceTimer = window.setTimeout(async () => {
        graceTimer = null;
        if (stopped) return;
        if (await isQuietContext()) return; // went quiet again during grace
        wasQuiet = false;
        const n = drainSuppressed();
        if (n > 0) onClear(n);
      }, GRACE_MS);
    }
  };

  const interval = window.setInterval(() => {
    void tick();
  }, POLL_MS);
  void tick();

  return () => {
    stopped = true;
    window.clearInterval(interval);
    clearGrace();
  };
}

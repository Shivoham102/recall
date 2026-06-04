import { useCallback, useEffect, useRef, useState } from "react";
import { emit, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { Window } from "@tauri-apps/api/window";
import { applyTheme, readStoredTheme } from "../hooks/useTheme";
import { Orb } from "./Orb/OrbCanvas";

// Renders in the borderless `notif` window. The card slides up, holds for exactly
// 5s (the ring around the X depletes over that window), then slides out and hides.
// It emits `recall:notif-dismissed` so the queue in services/notify.ts shows the
// next card (one at a time).
const VISIBLE_MS = 5000;
const EXIT_MS = 260;

interface NotifPayload {
  message: string;
}

export function NotificationCard() {
  const [message, setMessage] = useState("");
  const [shown, setShown] = useState(false);
  const [cycle, setCycle] = useState(0); // bumped per show to restart the ring animation
  const hideTimer = useRef<number | null>(null);
  const exitTimer = useRef<number | null>(null);

  const clearTimers = useCallback(() => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    if (exitTimer.current !== null) window.clearTimeout(exitTimer.current);
    hideTimer.current = null;
    exitTimer.current = null;
  }, []);

  const dismiss = useCallback(() => {
    clearTimers();
    setShown(false);
    exitTimer.current = window.setTimeout(() => {
      void invoke("hide_notif").catch(() => {});
      void emit("recall:notif-dismissed").catch(() => {});
      setMessage("");
    }, EXIT_MS);
  }, [clearTimers]);

  useEffect(() => {
    const unlisten = listen<NotifPayload>("recall:notif-show", (e) => {
      clearTimers();
      applyTheme(readStoredTheme()); // follow the user's currently-selected theme
      setMessage(e.payload.message);
      setCycle((c) => c + 1);
      requestAnimationFrame(() => setShown(true)); // next frame → enter transition
      hideTimer.current = window.setTimeout(dismiss, VISIBLE_MS);
    });
    return () => {
      unlisten.then((f) => f());
      clearTimers();
    };
  }, [clearTimers, dismiss]);

  const onCardClick = useCallback(async () => {
    try {
      const main = await Window.getByLabel("main");
      await main?.show();
      await main?.setFocus();
    } catch {
      /* ignore */
    }
    dismiss();
  }, [dismiss]);

  const onClose = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation(); // X dismisses only — don't also focus the main window
      dismiss();
    },
    [dismiss],
  );

  return (
    <div className="notif-root">
      <div className={`notif-card ${shown ? "notif-card--in" : ""}`} onClick={onCardClick}>
        <div className="notif-card__icon">
          <Orb state="idle" size={40} />
        </div>
        <div className="notif-card__msg">{message}</div>
        <button className="notif-card__close" onClick={onClose} aria-label="Dismiss" title="Dismiss">
          <svg width="28" height="28" viewBox="0 0 28 28">
            <circle className="notif-ring__track" cx="14" cy="14" r="11" />
            <circle key={cycle} className="notif-ring__progress" cx="14" cy="14" r="11" />
            <path className="notif-x" d="M10.5 10.5 L17.5 17.5 M17.5 10.5 L10.5 17.5" />
          </svg>
        </button>
      </div>
    </div>
  );
}

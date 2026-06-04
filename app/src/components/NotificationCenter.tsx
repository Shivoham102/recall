import { useEffect, useRef, useState } from "react";
import {
  NotificationEntry,
  clearNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  useNotifications,
} from "../services/notifications";

interface Props {
  /** Route to the entry's source (proactive → Agent tab, reminder → Reminders tab). */
  onNavigate: (entry: NotificationEntry) => void;
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function NotificationCenter({ onNavigate }: Props) {
  const { entries, unread } = useNotifications();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="notifc" ref={rootRef}>
      <button
        className="titlebar-icon-btn notifc__bell"
        onClick={() => setOpen((o) => !o)}
        title="Notifications"
        aria-label="Notifications"
      >
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
          <path
            d="M8 2a3.2 3.2 0 0 0-3.2 3.2c0 3-1.3 4-1.3 4h9s-1.3-1-1.3-4A3.2 3.2 0 0 0 8 2Z"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinejoin="round"
          />
          <path d="M6.7 12.4a1.4 1.4 0 0 0 2.6 0" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        {unread > 0 && <span className="notifc__badge">{unread > 9 ? "9+" : unread}</span>}
      </button>

      {open && (
        <div className="notifc__panel">
          <div className="notifc__head">
            <span className="notifc__title">Notifications</span>
            <div className="notifc__actions">
              {entries.some((e) => !e.read) && (
                <button className="notifc__action" onClick={markAllNotificationsRead}>
                  Mark all read
                </button>
              )}
              {entries.length > 0 && (
                <button className="notifc__action" onClick={clearNotifications}>
                  Clear
                </button>
              )}
            </div>
          </div>
          {entries.length === 0 ? (
            <div className="notifc__empty">Nothing yet.</div>
          ) : (
            <div className="notifc__list">
              {entries.map((e) => (
                <button
                  key={e.id}
                  className={`notifc__item ${e.read ? "" : "notifc__item--unread"}`}
                  onClick={() => {
                    markNotificationRead(e.id);
                    onNavigate(e);
                    setOpen(false);
                  }}
                >
                  <span className="notifc__msg">{e.message}</span>
                  <span className="notifc__time">{relativeTime(e.ts)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

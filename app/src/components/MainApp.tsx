import { useRef, useState, useEffect, ReactNode } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { AgentTab } from "./tabs/AgentTab";
import { TasksTab } from "./tabs/TasksTab";
// TranscriptsTab is intentionally kept in the codebase for possible audit/debug use.
// import { TranscriptsTab } from "./tabs/TranscriptsTab";
import { MemoryTab } from "./tabs/MemoryTab";
import { RemindersTab } from "./tabs/RemindersTab";
import { ProfileTab } from "./tabs/ProfileTab";
import { BrainIcon } from "./icons/BrainIcon";
import { loadPendingReminders } from "../services/reminderScheduler";
import { AuthUser } from "../hooks/useAuth";
import { AgentChatsProvider } from "../context/AgentChatsContext";

interface Props {
  user: AuthUser;
  onLogout: () => void;
}

type Tab = "agent" | "tasks" | "memory" | "reminders" | "profile";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: "agent",       label: "Agent",       icon: "◈" },
  { id: "tasks",       label: "Tasks",       icon: "◻" },
  // { id: "transcripts", label: "Transcripts", icon: "≡" },
  { id: "memory",      label: "Memory",      icon: <BrainIcon /> },
  { id: "reminders",   label: "Reminders",   icon: "◷" },
  { id: "profile",     label: "Profile",     icon: "◉" },
];

interface UpdateState {
  version: string;
  downloadedBytes: number;
  totalBytes: number;
  installing: boolean;
}

export function MainApp({ user, onLogout }: Props) {
  const [tab, setTab] = useState<Tab>("agent");
  const [updateOverlay, setUpdateOverlay] = useState<UpdateState | null>(null);
  const [upToDate, setUpToDate] = useState(false);
  const appWindow = useRef(getCurrentWindow()).current;

  useEffect(() => {
    loadPendingReminders();

    const onFocus = () => loadPendingReminders();
    const onVisibility = () => { if (document.visibilityState === "visible") loadPendingReminders(); };

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const unlisteners: (() => void)[] = [];

    (async () => {
      const [u1, u2, u3, u4] = await Promise.all([
        listen<string>("update-available", (e) => {
          if (cancelled) return;
          appWindow.show();
          appWindow.setFocus();
          setUpdateOverlay({ version: e.payload, downloadedBytes: 0, totalBytes: 0, installing: false });
        }),
        listen<{ downloaded: number; total: number }>("update-progress", (e) => {
          if (cancelled) return;
          setUpdateOverlay((prev) =>
            prev ? { ...prev, downloadedBytes: e.payload.downloaded, totalBytes: e.payload.total } : prev
          );
        }),
        listen<void>("update-download-done", () => {
          if (cancelled) return;
          setUpdateOverlay((prev) => (prev ? { ...prev, installing: true } : prev));
        }),
        listen<void>("update-not-found", () => {
          if (cancelled) return;
          setUpToDate(true);
          setTimeout(() => setUpToDate(false), 3000);
        }),
      ]);
      if (!cancelled) unlisteners.push(u1, u2, u3, u4);
      else unlisteners.forEach((u) => u());
    })();

    return () => {
      cancelled = true;
      unlisteners.forEach((u) => u());
    };
  }, []);

  return (
    <div className="main-app">
      <div className="titlebar" data-tauri-drag-region>
        <span className="titlebar__logo">
          <span className="titlebar__dot" />
          Recall
        </span>
        <div className="titlebar__tabs" data-tauri-drag-region>
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`titlebar__tab ${tab === t.id ? "titlebar__tab--active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              <span className="titlebar__tab-icon">{t.icon}</span>
              {t.label}
            </button>
          ))}
        </div>
        <div className="titlebar__controls">
          <button className="wm-btn" onClick={() => appWindow.minimize()} title="Minimize">─</button>
          <button className="wm-btn" onClick={() => appWindow.toggleMaximize()} title="Maximize">⬜</button>
          <button className="wm-btn wm-btn--close" onClick={() => appWindow.hide()} title="Hide to tray">✕</button>
        </div>
      </div>

      <AgentChatsProvider userId={user.user_id}>
        <div className="main-content">
          {tab === "agent"       && <AgentTab />}
          {tab === "tasks"       && <TasksTab />}
          {/* {tab === "transcripts" && <TranscriptsTab />} */}
          {tab === "memory"      && <MemoryTab />}
          {tab === "reminders"   && <RemindersTab />}
          {tab === "profile"     && <ProfileTab user={user} onLogout={onLogout} />}
        </div>
      </AgentChatsProvider>

      {updateOverlay && (
        <div className="update-overlay">
          <div className="update-card">
            <div className="update-card__title">Updating to v{updateOverlay.version}</div>
            {!updateOverlay.installing ? (
              <>
                <div className="update-progress-track">
                  <div
                    className="update-progress-bar"
                    style={{
                      width: updateOverlay.totalBytes > 0
                        ? `${Math.round((updateOverlay.downloadedBytes / updateOverlay.totalBytes) * 100)}%`
                        : "10%",
                    }}
                  />
                </div>
                <div className="update-status">
                  {updateOverlay.totalBytes > 0
                    ? `${(updateOverlay.downloadedBytes / 1048576).toFixed(1)} MB / ${(updateOverlay.totalBytes / 1048576).toFixed(1)} MB`
                    : "Downloading…"}
                </div>
              </>
            ) : (
              <div className="update-status">Installing… app will restart shortly.</div>
            )}
          </div>
        </div>
      )}

      {upToDate && (
        <div className="update-toast">Recall is up to date.</div>
      )}
    </div>
  );
}

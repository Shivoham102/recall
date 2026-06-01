import { useRef, useState, useEffect, ReactNode } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { AgentTab } from "./tabs/AgentTab";
import { TasksTab } from "./tabs/TasksTab";
// TranscriptsTab is intentionally kept in the codebase for possible audit/debug use.
// import { TranscriptsTab } from "./tabs/TranscriptsTab";
import { MemoryTab } from "./tabs/MemoryTab";
import { RemindersTab } from "./tabs/RemindersTab";
import { ProfileTab } from "./tabs/ProfileTab";
import { Onboarding } from "./Onboarding";
import { BrainIcon } from "./icons/BrainIcon";
import { loadPendingReminders } from "../services/reminderScheduler";
import { AuthUser } from "../hooks/useAuth";
import { useTheme } from "../hooks/useTheme";
import { AgentChatsProvider } from "../context/AgentChatsContext";

interface Props {
  user: AuthUser;
  onLogout: () => void;
}

type Tab = "agent" | "tasks" | "memory" | "reminders" | "profile";

const ONBOARDING_KEY = "recall_onboarding_complete";

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
  const [showOnboarding, setShowOnboarding] = useState<boolean>(() => {
    try { return localStorage.getItem(ONBOARDING_KEY) !== "1"; } catch { return true; }
  });
  const [updateOverlay, setUpdateOverlay] = useState<UpdateState | null>(null);
  const [pendingUpdate, setPendingUpdate] = useState<string | null>(null);
  const [upToDate, setUpToDate] = useState(false);
  const pendingVersionRef = useRef<string | null>(null);
  const appWindow = useRef(getCurrentWindow()).current;
  const { theme, toggleTheme } = useTheme();

  const closeOnboarding = () => {
    try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch { /* ignore */ }
    setShowOnboarding(false);
  };

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
          pendingVersionRef.current = e.payload;
          setPendingUpdate(e.payload);
        }),
        listen<{ downloaded: number; total: number }>("update-progress", (e) => {
          if (cancelled) return;
          setPendingUpdate(null);
          setUpdateOverlay((prev) =>
            prev
              ? { ...prev, downloadedBytes: e.payload.downloaded, totalBytes: e.payload.total }
              : { version: pendingVersionRef.current ?? "", downloadedBytes: e.payload.downloaded, totalBytes: e.payload.total, installing: false }
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
        <button
          className="titlebar-icon-btn"
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          aria-label="Toggle theme"
          onClick={toggleTheme}
        >
          {theme === "dark" ? (
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.4" />
              <path d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5L3.4 3.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M13.5 9.8A5.6 5.6 0 0 1 6.2 2.5a5.6 5.6 0 1 0 7.3 7.3Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
          )}
        </button>
        <button
          className={`update-btn${pendingUpdate ? " update-btn--dot" : ""}`}
          title={pendingUpdate ? `Update to v${pendingUpdate}` : "Check for updates"}
          onClick={async () => {
            if (pendingUpdate) {
              setPendingUpdate(null);
              await invoke("start_update");
            } else if (!updateOverlay) {
              await invoke("check_for_updates");
            }
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 3V9M7 9L4 6M7 9L10 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            <path d="M2 12H12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>
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
          {tab === "profile"     && <ProfileTab user={user} onLogout={onLogout} onShowOnboarding={() => setShowOnboarding(true)} />}
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

      {showOnboarding && <Onboarding onClose={closeOnboarding} />}
    </div>
  );
}

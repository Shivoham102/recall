import { useRef, useState, useEffect, ReactNode } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
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

export function MainApp({ user, onLogout }: Props) {
  const [tab, setTab] = useState<Tab>("agent");
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
    </div>
  );
}

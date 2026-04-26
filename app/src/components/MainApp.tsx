import React, { useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { AgentTab } from "./tabs/AgentTab";
import { TasksTab } from "./tabs/TasksTab";
import { TranscriptsTab } from "./tabs/TranscriptsTab";
import { RemindersTab } from "./tabs/RemindersTab";

type Tab = "agent" | "tasks" | "transcripts" | "reminders";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "agent",       label: "Agent",       icon: "◈" },
  { id: "tasks",       label: "Tasks",       icon: "◻" },
  { id: "transcripts", label: "Transcripts", icon: "≡" },
  { id: "reminders",   label: "Reminders",   icon: "◷" },
];

export function MainApp() {
  const [tab, setTab] = useState<Tab>("agent");
  const appWindow = useRef(getCurrentWindow()).current;

  return (
    <div className="main-app">
      {/* Titlebar */}
      <div className="titlebar">
        <span className="titlebar__logo" data-tauri-drag-region>
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
              <span>{t.icon}</span>
              {t.label}
            </button>
          ))}
        </div>
        <div className="titlebar__controls">
          <button className="wm-btn" onClick={() => appWindow.minimize()} title="Minimize">─</button>
          <button className="wm-btn wm-btn--close" onClick={() => appWindow.hide()} title="Close">✕</button>
        </div>
      </div>

      {/* Content */}
      <div className="main-content">
        {tab === "agent"       && <AgentTab />}
        {tab === "tasks"       && <TasksTab />}
        {tab === "transcripts" && <TranscriptsTab />}
        {tab === "reminders"   && <RemindersTab />}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listen, emit } from "@tauri-apps/api/event";
import { captureStream, playAudio } from "../../services/api";
import { useRecorder } from "../../hooks/useRecorder";
import { useAgentChats } from "../../context/AgentChatsContext";
import { AgentStep, EmailCard, TaskCard } from "../../types/agentTurn";
import { scheduleReminder } from "../../services/reminderScheduler";
import { AgentChatSidebar } from "./AgentChatSidebar";

const TASK_TYPE_COLOR: Record<string, string> = {
  blocker: "#ff4466",
  task: "#00e5ff",
  follow_up: "#ff9900",
  progress: "#00ff88",
  note: "#9988ff",
};

const INTENT_COLORS: Record<string, string> = {
  task: "#00e5ff",
  blocker: "#ff4466",
  follow_up: "#ff9900",
  progress: "#00ff88",
  note: "#9988ff",
  query: "#aaaaaa",
  update: "#ffcc00",
};

const STEP_LABELS: Record<string, string> = {
  gmail_get_updates: "checking inbox",
  surface_cards: "selecting highlights",
  gmail_find_contact: "looking up contact",
  gmail_fetch_style_samples: "reading writing style",
  gmail_draft: "saving draft",
  recall_search: "searching memory",
  surface_tasks: "selecting tasks",
  recall_update_item: "updating task",
  calendar_list: "checking calendar",
  calendar_create: "creating event",
  file_create: "creating file",
  classify_intent: "classifying",
};

function StepsGroup({ steps }: { steps: AgentStep[] }) {
  const [open, setOpen] = useState(false);
  const done = steps.filter((s) => !s.pending);
  const anyPending = steps.some((s) => s.pending);

  return (
    <div className="steps-group">
      <button className="steps-toggle" onClick={() => setOpen((o) => !o)}>
        <span className="steps-toggle__arrow">{open ? "▾" : "▸"}</span>
        <span className="steps-toggle__label">
          {anyPending ? "working..." : `${done.length} step${done.length !== 1 ? "s" : ""}`}
        </span>
      </button>
      {open && (
        <div className="steps-list">
          {steps.map((step) => (
            <div key={`${step.name}-${step.summary}`} className={`step-row${step.pending ? " step-row--pending" : ""}`}>
              <span className="step-row__icon">{step.pending ? "o" : "✓"}</span>
              <span className="step-row__label">{STEP_LABELS[step.name] ?? step.name}</span>
              {step.summary && <span className="step-row__summary">· {step.summary}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EmailCardGrid({ cards }: { cards: EmailCard[] }) {
  return (
    <div className="email-cards">
      {cards.map((card, i) => (
        <div key={`${card.subject}-${i}`} className={`email-card${card.unread ? " email-card--unread" : ""}`}>
          <div className="email-card__header">
            <span className="email-card__sender">{card.sender}</span>
            <span className="email-card__time">{card.received}</span>
          </div>
          <div className="email-card__subject">{card.subject}</div>
          {card.snippet && <div className="email-card__snippet">{card.snippet}</div>}
          {card.unread && <div className="email-card__badge">unread</div>}
        </div>
      ))}
    </div>
  );
}

function TaskCardGrid({ cards }: { cards: TaskCard[] }) {
  function fmt(iso: string) {
    const d = new Date(iso);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
    if (diffDays === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  return (
    <div className="agent-task-cards">
      {cards.map((card) => {
        const color = TASK_TYPE_COLOR[card.intent_type] ?? "#aaa";
        return (
          <div key={card.id} className="task-card">
            <span className="task-card__badge" style={{ color, borderColor: color }}>
              {card.intent_type.replace("_", " ")}
            </span>
            <p className="task-card__content">{card.content}</p>
            <div className="task-card__footer">
              <span className="task-card__date">{fmt(card.created_at)}</span>
              {card.due_hint && <span className="task-card__due">due: {card.due_hint}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function AgentTab() {
  const {
    chats,
    activeChat,
    activeChatId,
    loading,
    loadError,
    saveError,
    sidebarPinned,
    setSidebarPinned,
    setActiveChatId,
    createChat,
    renameChat,
    deleteChat,
    loadMore,
    hasMore,
    replaceChatTurns,
    patchTurnInChat,
    refreshChatTitleFromServer,
  } = useAgentChats();

  const [orbError, setOrbError] = useState(false);
  const [sidebarPeek, setSidebarPeek] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  /** After unpin: ignore edge-hover peek until cursor moves away from the left strip. */
  const suppressEdgePeekRef = useRef(false);
  const recorder = useRecorder();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeChat?.turns]);

  const activeTurns = activeChat?.turns ?? [];
  const activeSessionId = activeChat?.agent_session_id;

  useEffect(() => {
    const unlisten = listen<{ items: { id: string; content: string }[] }>(
      "recall:reminders-missed",
      (e) => {
        if (!activeChatId) return;
        const list = e.payload.items.map((i) => i.content).join(", ");
        replaceChatTurns(activeChatId, (prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "system", text: `${e.payload.items.length} reminder(s) missed while closed: ${list}` },
        ]);
      },
    );
    return () => { unlisten.then((f) => f()); };
  }, [activeChatId, replaceChatTurns]);

  useEffect(() => {
    const unlisten = listen("recall:reminder-failed", () => {
      if (!activeChatId) return;
      replaceChatTurns(activeChatId, (prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "system", text: "A reminder failed to deliver. Check your Reminders tab." },
      ]);
    });
    return () => { unlisten.then((f) => f()); };
  }, [activeChatId, replaceChatTurns]);

  const handleCapture = useCallback(async (blob: Blob) => {
    if (!activeChat || !activeSessionId) return;
    const chatId = activeChat.id;
    const userTurnId = crypto.randomUUID();
    const assistantTurnId = crypto.randomUUID();

    replaceChatTurns(chatId, (prev) => [
      ...prev,
      { id: userTurnId, role: "user", text: "" },
      { id: assistantTurnId, role: "assistant", text: "", steps: [], pending: true },
    ]);

    let doneHandled = false;
    try {
      let transcript = "";
      let spokenText = "";
      let intentType: string | undefined;
      let itemId: string | null = null;
      let dueAt: string | null = null;
      let audiob64 = "";

      recorder.setSpeaking();

      for await (const event of captureStream(blob, activeSessionId)) {
        if (event.type === "transcript") {
          transcript = event.text;
          patchTurnInChat(chatId, userTurnId, { text: transcript });
        } else if (event.type === "tool_call") {
          const call = { name: event.name, summary: "", pending: true };
          replaceChatTurns(chatId, (prev) =>
            prev.map((turn) =>
              turn.id === assistantTurnId
                ? { ...turn, steps: [...(turn.steps ?? []), call] }
                : turn,
            ),
          );
        } else if (event.type === "tool_result") {
          replaceChatTurns(chatId, (prev) =>
            prev.map((turn) => {
              if (turn.id !== assistantTurnId) return turn;
              const steps = [...(turn.steps ?? [])];
              const idx = [...steps].reverse().findIndex((s) => s.name === event.name && s.pending);
              if (idx !== -1) {
                const realIdx = steps.length - 1 - idx;
                steps[realIdx] = { ...steps[realIdx], summary: event.summary, pending: false };
              }
              const next = { ...turn, steps };
              if (event.name === "surface_cards" && Array.isArray(event.data?.items_data)) {
                next.emailCards = event.data.items_data as EmailCard[];
              }
              if (event.name === "surface_tasks" && Array.isArray(event.data?.items_data)) {
                next.taskCards = event.data.items_data as TaskCard[];
              }
              return next;
            }),
          );
        } else if (event.type === "ack_audio") {
          playAudio(event.audio_base64);
        } else if (event.type === "spoken") {
          spokenText = event.text;
          patchTurnInChat(chatId, assistantTurnId, { text: spokenText });
        } else if (event.type === "metadata") {
          intentType = event.intent_type;
          patchTurnInChat(chatId, userTurnId, { intentType });
        } else if (event.type === "stored") {
          itemId = event.item_id;
          dueAt = event.due_at;
        } else if (event.type === "audio") {
          audiob64 = event.audio_base64;
        } else if (event.type === "error") {
          patchTurnInChat(chatId, assistantTurnId, { text: event.message, pending: false });
        } else if (event.type === "done") {
          doneHandled = true;
          patchTurnInChat(chatId, assistantTurnId, { pending: false });
          if (audiob64) {
            playAudio(audiob64, () => recorder.reset());
          } else {
            recorder.reset();
          }
          if (dueAt && itemId) scheduleReminder(itemId, dueAt);
          await emit("recall:new-turn", {
            transcript,
            response_text: spokenText,
            intent_type: intentType,
            item_id: itemId,
          });
          window.setTimeout(() => {
            void refreshChatTitleFromServer(chatId);
          }, 0);
        }
      }
    } catch (err) {
      console.error("[AgentTab] capture failed:", err);
      patchTurnInChat(chatId, assistantTurnId, { text: "Something went wrong.", pending: false });
      setOrbError(true);
      setTimeout(() => { setOrbError(false); recorder.reset(); }, 2000);
    } finally {
      if (!doneHandled && recorder.state === "speaking") recorder.reset();
    }
  }, [activeChat, activeSessionId, patchTurnInChat, recorder, replaceChatTurns, refreshChatTitleFromServer]);

  const handleStop = useCallback(async () => {
    let blob: Blob;
    try {
      blob = await recorder.stop();
    } catch {
      setOrbError(true);
      setTimeout(() => { setOrbError(false); recorder.reset(); }, 2000);
      return;
    }
    await handleCapture(blob);
  }, [handleCapture, recorder]);

  const lastToggleMs = useRef(0);
  const handleToggle = useCallback(() => {
    const now = Date.now();
    if (now - lastToggleMs.current < 600) return;
    lastToggleMs.current = now;
    if (recorder.state === "idle") {
      recorder.start().catch(() => {});
    } else if (recorder.state === "recording") {
      void handleStop();
    }
  }, [handleStop, recorder]);

  const orbState = orbError ? "error" : recorder.state;
  const sidebarVisible = sidebarPinned || sidebarPeek;

  useEffect(() => {
    if (!rootRef.current || sidebarPinned) return;
    const el = rootRef.current;
    const onMove = (e: MouseEvent) => {
      const rect = el.getBoundingClientRect();
      const x = e.clientX - rect.left;
      if (x > 56) suppressEdgePeekRef.current = false;
      if (suppressEdgePeekRef.current) return;
      if (x <= 14) setSidebarPeek(true);
    };
    el.addEventListener("mousemove", onMove);
    return () => el.removeEventListener("mousemove", onMove);
  }, [sidebarPinned]);

  const chatsSorted = useMemo(
    () => [...chats].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [chats],
  );

  if (loading) return <div className="tab-loading">Loading chats...</div>;

  return (
    <div
      className={`agent-layout${sidebarPinned ? " agent-layout--sidebar-pinned" : ""}`}
      ref={rootRef}
    >
      {!sidebarPinned && (
        <div
          className="agent-sidebar-hitstrip"
          onMouseEnter={() => {
            if (!suppressEdgePeekRef.current) setSidebarPeek(true);
          }}
        />
      )}

      <AgentChatSidebar
        chats={chatsSorted}
        activeChatId={activeChatId}
        visible={sidebarVisible}
        pinned={sidebarPinned}
        loadError={loadError}
        saveError={saveError}
        hasMore={hasMore}
        onTogglePinned={() => {
          if (sidebarPinned) {
            suppressEdgePeekRef.current = true;
            setSidebarPeek(false);
            setSidebarPinned(false);
          } else {
            setSidebarPeek(false);
            setSidebarPinned(true);
          }
        }}
        onNewChat={() => { createChat({ activate: true }); }}
        onSelectChat={(chatId) => setActiveChatId(chatId)}
        onRenameChat={(chatId, title) => { void renameChat(chatId, title); }}
        onDeleteChat={(chatId) => { void deleteChat(chatId); }}
        onLoadMore={() => { void loadMore(); }}
        onPointerEnter={() => {
          if (!suppressEdgePeekRef.current) setSidebarPeek(true);
        }}
        onPointerLeave={() => {
          if (!sidebarPinned) setSidebarPeek(false);
        }}
      />

      <div className="agent-tab">
        <div className="agent-hint">
          Press <kbd>Ctrl+Shift+Space</kbd> or click the orb to start speaking
        </div>

        <div className="agent-turns">
          {activeTurns.length === 0 && (
            <div className="agent-empty">
              <span className="agent-empty__icon">◈</span>
              <p>Your conversation will appear here.</p>
            </div>
          )}
          {activeTurns.map((t) => (
            <div key={t.id} className={`turn turn--${t.role}${t.pending ? " turn--pending" : ""}`}>
              {t.intentType && t.role === "user" && (
                <span
                  className="turn__badge"
                  style={{ color: INTENT_COLORS[t.intentType] ?? "#aaa", borderColor: INTENT_COLORS[t.intentType] ?? "#aaa" }}
                >
                  {t.intentType.replace("_", " ")}
                </span>
              )}
              {t.steps && t.steps.length > 0 && <StepsGroup steps={t.steps} />}
              {t.text && <p>{t.text}</p>}
              {t.emailCards && t.emailCards.length > 0 && <EmailCardGrid cards={t.emailCards} />}
              {t.taskCards && t.taskCards.length > 0 && <TaskCardGrid cards={t.taskCards} />}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="agent-orb-root">
          <div className={`orb-glow orb-glow--${orbState}`} />
          <div className={`orb-core orb-core--${orbState}`} onClick={handleToggle}>
            <div className="orb-shimmer" />
            <div className="orb-specular" />
            <div className="orb-scanlines" />
          </div>
        </div>
      </div>
    </div>
  );
}

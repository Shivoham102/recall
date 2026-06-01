import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { listen, emit } from "@tauri-apps/api/event";
import { openUrl } from "@tauri-apps/plugin-opener";
import { captureStream, playAudio, getItems, RecallItem } from "../../services/api";
import { StreamingAudioPlayer } from "../../services/audioPlayer";
import { formatDue, formatRecurrence } from "../../utils/dateFormat";
import { useRecorder } from "../../hooks/useRecorder";
import { Orb } from "../Orb/OrbCanvas";
import { TabLoading } from "../TabLoading";
import { useAgentChats } from "../../context/AgentChatsContext";
import { AgentStep, CalendarCard, EmailCard, TaskCard } from "../../types/agentTurn";
import { scheduleReminder } from "../../services/reminderScheduler";
import { applyItemUpdatedTimer } from "../../services/itemUpdatedTimer";
import { AgentChatSidebar } from "./AgentChatSidebar";
import { coalesceProactiveTurns } from "../../utils/agentChatDisplay";

// Theme-aware: each value resolves to a CSS var that differs per light/dark
// theme (see --intent-* tokens in App.css) so badges stay legible on both.
const TASK_TYPE_COLOR: Record<string, string> = {
  blocker: "var(--intent-blocker)",
  task: "var(--intent-task)",
  follow_up: "var(--intent-follow_up)",
  follow_up_draft: "var(--intent-follow_up_draft)",
  progress: "var(--intent-progress)",
  note: "var(--intent-note)",
};

const INTENT_COLORS: Record<string, string> = {
  task: "var(--intent-task)",
  blocker: "var(--intent-blocker)",
  follow_up: "var(--intent-follow_up)",
  follow_up_draft: "var(--intent-follow_up_draft)",
  progress: "var(--intent-progress)",
  note: "var(--intent-note)",
  query: "var(--intent-query)",
  update: "var(--intent-update)",
};

const PROACTIVE_BADGE_LABELS: Record<string, string> = {
  morning_brief: "MORNING BRIEF",
  email_triage: "EMAIL TRIAGE",
  follow_up_scan: "FOLLOW-UP",
  follow_up_draft: "DRAFT READY",
  pattern_learn: "PATTERN",
};

function fmtProactiveTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const timeStr = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `Today ${timeStr}`;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${timeStr}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${timeStr}`;
}

const STEP_LABELS: Record<string, string> = {
  gmail_get_updates: "checking inbox",
  surface_cards: "selecting highlights",
  surface_calendar: "showing events",
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

function DayDivider({ timestamp }: { timestamp: string }) {
  const d = new Date(timestamp);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  let label: string;
  if (d.toDateString() === now.toDateString()) label = "Today";
  else if (d.toDateString() === yesterday.toDateString()) label = "Yesterday";
  else label = d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  return <div className="day-divider"><span className="day-divider__label">{label}</span></div>;
}

function EmailCardGrid({ cards }: { cards: EmailCard[] }) {
  return (
    <div className="email-cards">
      {cards.map((card, i) => (
        <div
          key={`${card.subject}-${i}`}
          className={`email-card${card.unread ? " email-card--unread" : ""}`}
          onClick={() => card.link && openUrl(card.link).catch(() => {})}
          style={{ cursor: card.link ? "pointer" : "default" }}
        >
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

function CalendarCardGrid({ cards }: { cards: CalendarCard[] }) {
  function fmt(iso: string, isAllDay: boolean) {
    if (!iso) return "";
    if (isAllDay) {
      const d = new Date(iso + "T00:00:00");
      return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }).toUpperCase();
    }
    const d = new Date(iso);
    const now = new Date();
    const timeStr = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).toUpperCase();
    if (d.toDateString() === now.toDateString()) return `TODAY ${timeStr}`;
    return `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }).toUpperCase()} ${timeStr}`;
  }

  return (
    <div className="calendar-cards">
      {cards.map((card, i) => (
        <div
          key={`${card.title}-${i}`}
          className="calendar-card"
          onClick={() => card.link && openUrl(card.link).catch(() => {})}
          style={{ cursor: card.link ? "pointer" : "default" }}
        >
          <div className="calendar-card__time">
            <span className="calendar-card__time-icon">◷</span>
            {fmt(card.start, card.is_all_day)}
          </div>
          <div className="calendar-card__title">{card.title}</div>
          {card.location && <div className="calendar-card__location">📍 {card.location}</div>}
        </div>
      ))}
    </div>
  );
}

function TaskCardGrid({ cards, liveItems }: { cards: TaskCard[]; liveItems: Map<string, RecallItem> }) {
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
        const live = liveItems.get(card.id);
        const color = TASK_TYPE_COLOR[card.intent_type] ?? "var(--intent-query)";
        const content = live?.display_text ?? live?.content ?? card.content;
        const dueItem = live ?? card;
        const timestamp = live?.updated_at ?? card.created_at;
        const isStale = !card.intent_type.startsWith("follow_up") && !live;
        return (
          <div
            key={card.id}
            className="task-card"
            onClick={() => card.link && openUrl(card.link).catch(() => {})}
            style={{ cursor: card.link ? "pointer" : "default", opacity: isStale ? 0.5 : 1 }}
          >
            <span className="task-card__badge" style={{ color, borderColor: color }}>
              {card.intent_type.replace("_", " ")}
              {isStale && " · resolved"}
            </span>
            <p className="task-card__content">{content}</p>
            <div className="task-card__footer">
              <span className="task-card__date">{fmt(timestamp)}</span>
              {dueItem.recurrence ? (
                <span className="task-card__due">{formatRecurrence(dueItem.recurrence).toLowerCase()}</span>
              ) : (dueItem.due_hint || dueItem.due_at) ? (
                <span className="task-card__due">due: {formatDue(dueItem).toLowerCase()}</span>
              ) : null}
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
    proactiveUnread,
    clearProactiveUnread,
  } = useAgentChats();

  const [orbError, setOrbError] = useState(false);
  const [sidebarPeek, setSidebarPeek] = useState(false);
  const [liveItems, setLiveItems] = useState<Map<string, RecallItem>>(new Map());
  const bottomRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const currentAckPlayerRef = useRef<StreamingAudioPlayer | null>(null);
  const currentFinalPlayerRef = useRef<StreamingAudioPlayer | null>(null);

  const refreshLiveItems = useCallback(async () => {
    try {
      const items = await getItems({ status: "open" });
      setLiveItems(new Map(items.map((i) => [i.id, i])));
    } catch {
      // best-effort
    }
  }, []);

  useEffect(() => { void refreshLiveItems(); }, [refreshLiveItems]);

  useEffect(() => {
    const unlisten = listen("recall:new-turn", () => { void refreshLiveItems(); });
    return () => { unlisten.then((f) => f()); };
  }, [refreshLiveItems]);
  /** After unpin: ignore edge-hover peek until cursor moves away from the left strip. */
  const suppressEdgePeekRef = useRef(false);
  const recorder = useRecorder();

  const prevChatIdRef = useRef<string | undefined>(undefined);
  useLayoutEffect(() => {
    if (prevChatIdRef.current !== activeChatId) {
      prevChatIdRef.current = activeChatId ?? undefined;
      bottomRef.current?.scrollIntoView({ behavior: "instant" });
    }
  }, [activeChatId]);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeChat?.turns]);

  const activeTurns = activeChat?.turns ?? [];
  const displayTurns = useMemo(() => coalesceProactiveTurns(activeTurns), [activeTurns]);
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

    // Abort any players from a prior incomplete request
    currentAckPlayerRef.current?.abort();
    currentFinalPlayerRef.current?.abort();
    currentAckPlayerRef.current = null;
    currentFinalPlayerRef.current = null;

    replaceChatTurns(chatId, (prev) => [
      ...prev,
      { id: userTurnId, role: "user", text: "", pending: true },
      { id: assistantTurnId, role: "assistant", text: "", steps: [], pending: true },
    ]);

    let doneHandled = false;
    try {
      let transcript = "";
      let spokenText = "";
      let streamingText = "";
      let intentType: string | undefined;
      let awaitingClarification = false;
      let itemId: string | null = null;
      let dueAt: string | null = null;
      let ackPlayer: StreamingAudioPlayer | null = null;
      let finalPlayer: StreamingAudioPlayer | null = null;

      // Stay in "processing" (thinking) through transcript + tool calls; only
      // flip to "speaking" when the final audio actually starts (below).

      for await (const event of captureStream(blob, activeSessionId)) {
        if (event.type === "transcript") {
          transcript = event.text;
          patchTurnInChat(chatId, userTurnId, { text: transcript, pending: false });
        } else if (event.type === "token") {
          streamingText += event.text;
          patchTurnInChat(chatId, assistantTurnId, { text: streamingText });
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
              if (event.name === "surface_calendar" && Array.isArray(event.data?.items_data)) {
                next.calendarCards = event.data.items_data as CalendarCard[];
              }
              if (event.name === "surface_tasks" && Array.isArray(event.data?.items_data)) {
                next.taskCards = event.data.items_data as TaskCard[];
              }
              return next;
            }),
          );
        } else if (event.type === "ack_audio") {
          playAudio(event.audio_base64);
        } else if (event.type === "ack_audio_chunk") {
          if (!ackPlayer) {
            ackPlayer = new StreamingAudioPlayer();
            currentAckPlayerRef.current = ackPlayer;
          }
          ackPlayer.append(event.data);
        } else if (event.type === "ack_audio_done") {
          ackPlayer?.done();
          ackPlayer = null;
          currentAckPlayerRef.current = null;
        } else if (event.type === "spoken") {
          spokenText = event.text;
          streamingText = "";
          patchTurnInChat(chatId, assistantTurnId, { text: spokenText });
        } else if (event.type === "metadata") {
          intentType = event.intent_type;
          awaitingClarification = event.awaiting_clarification ?? false;
          patchTurnInChat(chatId, userTurnId, { intentType });
        } else if (event.type === "stored") {
          itemId = event.item_id;
          dueAt = event.due_at;
        } else if (event.type === "item_updated") {
          applyItemUpdatedTimer(event.item_id, event.due_at, "AgentTab");
        } else if (event.type === "audio") {
          // Legacy batch audio path — kept for backward compat
          if (!awaitingClarification) recorder.setSpeaking();
          playAudio(event.audio_base64, () => {
            if (awaitingClarification) {
              recorder.reset();
              recorder.start().catch(() => {});
            } else {
              recorder.reset();
            }
          });
        } else if (event.type === "audio_chunk") {
          if (!finalPlayer) {
            if (!awaitingClarification) recorder.setSpeaking();
            finalPlayer = new StreamingAudioPlayer(() => {
              currentFinalPlayerRef.current = null;
              if (awaitingClarification) {
                recorder.reset();
                recorder.start().catch(() => {});
              } else {
                recorder.reset();
              }
            });
            currentFinalPlayerRef.current = finalPlayer;
          }
          finalPlayer.append(event.data);
        } else if (event.type === "audio_done") {
          finalPlayer?.done();
        } else if (event.type === "error") {
          patchTurnInChat(chatId, assistantTurnId, { text: event.message, pending: false });
        } else if (event.type === "done") {
          doneHandled = true;
          patchTurnInChat(chatId, assistantTurnId, { pending: false });
          if (!awaitingClarification) {
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
          // recorder.reset() lives in finalPlayer.onEnd for streaming audio path.
          // If no audio was synthesized, reset/re-arm immediately.
          if (!finalPlayer) {
            if (awaitingClarification) {
              recorder.reset();
              recorder.start().catch(() => {});
            } else {
              recorder.reset();
            }
          }
        }
      }
    } catch (err) {
      console.error("[AgentTab] capture failed:", err);
      patchTurnInChat(chatId, assistantTurnId, { text: "Something went wrong.", pending: false });
      setOrbError(true);
      setTimeout(() => { setOrbError(false); recorder.reset(); }, 2000);
    } finally {
      if (!doneHandled && (recorder.state === "speaking" || recorder.state === "processing")) recorder.reset();
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

  const turnsWithMeta = useMemo(() => {
    let lastDate = "";
    return displayTurns.map((t) => {
      let showDivider = false;
      if (t.timestamp) {
        const d = new Date(t.timestamp).toDateString();
        if (d !== lastDate) { lastDate = d; showDivider = true; }
      }
      return { turn: t, showDivider };
    });
  }, [displayTurns]);

  // Proactive inbox chat always pinned first; remaining chats sorted by updated_at
  const chatsSorted = useMemo(() => {
    const inbox = chats.filter((c) => c.is_proactive_inbox);
    const rest = chats
      .filter((c) => !c.is_proactive_inbox)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return [...inbox, ...rest];
  }, [chats]);

  if (loading) return <TabLoading />;

  return (
    <div
      className={`agent-layout${sidebarPinned ? " agent-layout--sidebar-pinned" : ""}`}
      ref={rootRef}
    >
      {!sidebarPinned && (
        <>
          <div
            className="agent-sidebar-hitstrip"
            onMouseEnter={() => {
              if (!suppressEdgePeekRef.current) setSidebarPeek(true);
            }}
          />
          {!sidebarVisible && (
            <button
              type="button"
              className="agent-sidebar-expand"
              title="Open chats"
              aria-label="Open chats"
              onClick={() => {
                suppressEdgePeekRef.current = false;
                setSidebarPeek(true);
              }}
              onMouseEnter={() => {
                if (!suppressEdgePeekRef.current) setSidebarPeek(true);
              }}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
                <path d="M4 2.5L8 6L4 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              {proactiveUnread && (
                <span className="agent-sidebar-expand__dot" aria-label="New proactive updates" />
              )}
            </button>
          )}
        </>
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
        proactiveUnread={proactiveUnread}
        onSelectChat={(chatId) => {
          setActiveChatId(chatId);
          const chat = chats.find((c) => c.id === chatId);
          if (chat?.is_proactive_inbox && proactiveUnread) clearProactiveUnread();
        }}
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
        <div className="agent-turns">
          {displayTurns.length === 0 && (
            <div className="agent-empty">
              <span className="agent-empty__icon">◈</span>
              <p>Your conversation will appear here.</p>
            </div>
          )}
          {turnsWithMeta.map(({ turn: t, showDivider }) => (
            <Fragment key={t.id}>
              {showDivider && t.timestamp && <DayDivider timestamp={t.timestamp} />}
              <div className={`turn turn--${t.role}${t.pending ? " turn--pending" : ""}`}>
                {t.role === "proactive" ? (
                  <div className="turn__proactive-header">
                    <span className="turn__proactive-badge">
                      {PROACTIVE_BADGE_LABELS[t.intentType ?? ""] ?? (t.intentType?.toUpperCase() ?? "RECALL")}
                    </span>
                    {t.timestamp && (
                      <span className="turn__proactive-time">◷ {fmtProactiveTime(t.timestamp)}</span>
                    )}
                  </div>
                ) : (
                  t.intentType && t.role === "user" && (
                    <span
                      className="turn__badge"
                      style={{ color: INTENT_COLORS[t.intentType] ?? "var(--intent-query)" }}
                    >
                      {t.intentType.replace("_", " ")}
                    </span>
                  )
                )}
                {t.role !== "proactive" && t.steps && t.steps.length > 0 && <StepsGroup steps={t.steps} />}
                {t.text
                  ? <p>{t.text}</p>
                  : (t.role === "user" && t.pending ? <p className="turn__recording">···</p> : null)
                }
                {t.emailCards && t.emailCards.length > 0 && <EmailCardGrid cards={t.emailCards} />}
                {t.calendarCards && t.calendarCards.length > 0 && <CalendarCardGrid cards={t.calendarCards} />}
                {t.taskCards && t.taskCards.length > 0 && <TaskCardGrid cards={t.taskCards} liveItems={liveItems} />}
                {t.role === "proactive" && t.intentType === "morning_brief" && (
                  <div className="morning-brief-drafts">
                    <div className="morning-brief-drafts__label">Drafted follow-ups</div>
                    {t.morningBriefDraftCards && t.morningBriefDraftCards.length > 0 ? (
                      <TaskCardGrid cards={t.morningBriefDraftCards} liveItems={liveItems} />
                    ) : (
                      <p className="morning-brief-drafts__empty">No drafts for today</p>
                    )}
                  </div>
                )}
              </div>
            </Fragment>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="agent-orb-root">
          <Orb state={orbState} size={88} onClick={handleToggle} />
        </div>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState, useCallback } from "react";
import { listen } from "@tauri-apps/api/event";
import { emit } from "@tauri-apps/api/event";
import { captureStream, playAudio, getOrCreateSessionId } from "../../services/api";
import { useRecorder } from "../../hooks/useRecorder";
import { scheduleReminder } from "../../services/reminderScheduler";

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentStep {
  name: string;
  summary: string;
  pending: boolean;
}

interface EmailCard {
  sender: string;
  subject: string;
  snippet: string;
  received: string;
  unread: boolean;
  important: boolean;
}

interface TaskCard {
  id: string;
  content: string;
  intent_type: string;
  status: string;
  created_at: string;
  due_hint?: string | null;
}

interface Turn {
  role: "user" | "assistant" | "system";
  text: string;
  intentType?: string;
  steps?: AgentStep[];
  emailCards?: EmailCard[];
  taskCards?: TaskCard[];
  pending?: boolean;
}

// ── Constants ────────────────────────────────────────────────────────────────

const TASK_TYPE_COLOR: Record<string, string> = {
  blocker:   "#ff4466",
  task:      "#00e5ff",
  follow_up: "#ff9900",
  progress:  "#00ff88",
  note:      "#9988ff",
};

const INTENT_COLORS: Record<string, string> = {
  task:      "#00e5ff",
  blocker:   "#ff4466",
  follow_up: "#ff9900",
  progress:  "#00ff88",
  note:      "#9988ff",
  query:     "#aaaaaa",
  update:    "#ffcc00",
};

const STEP_LABELS: Record<string, string> = {
  gmail_get_updates:       "checking inbox",
  surface_cards:           "selecting highlights",
  gmail_find_contact:      "looking up contact",
  gmail_fetch_style_samples: "reading writing style",
  gmail_draft:             "saving draft",
  recall_search:           "searching memory",
  surface_tasks:           "selecting tasks",
  recall_update_item:      "updating task",
  calendar_list:           "checking calendar",
  calendar_create:         "creating event",
  file_create:             "creating file",
  classify_intent:         "classifying",
};

// ── Sub-components ───────────────────────────────────────────────────────────

function StepsGroup({ steps }: { steps: AgentStep[] }) {
  const [open, setOpen] = useState(false);
  const done = steps.filter((s) => !s.pending);
  const anyPending = steps.some((s) => s.pending);

  return (
    <div className="steps-group">
      <button className="steps-toggle" onClick={() => setOpen((o) => !o)}>
        <span className="steps-toggle__arrow">{open ? "▾" : "▸"}</span>
        <span className="steps-toggle__label">
          {anyPending ? "working…" : `${done.length} step${done.length !== 1 ? "s" : ""}`}
        </span>
      </button>
      {open && (
        <div className="steps-list">
          {steps.map((step, i) => (
            <div key={i} className={`step-row${step.pending ? " step-row--pending" : ""}`}>
              <span className="step-row__icon">{step.pending ? "○" : "✓"}</span>
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
        <div key={i} className={`email-card${card.unread ? " email-card--unread" : ""}`}>
          <div className="email-card__header">
            <span className="email-card__sender">{card.sender}</span>
            <span className="email-card__time">{card.received}</span>
          </div>
          <div className="email-card__subject">{card.subject}</div>
          {card.snippet && (
            <div className="email-card__snippet">{card.snippet}</div>
          )}
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

// ── Main component ───────────────────────────────────────────────────────────

export function AgentTab() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [orbError, setOrbError] = useState(false);
  const sessionId = useRef(getOrCreateSessionId()).current;
  const bottomRef = useRef<HTMLDivElement>(null);
  const recorder = useRecorder();

  useEffect(() => {
    const unlisten = listen<{ items: { id: string; content: string }[] }>(
      "recall:reminders-missed",
      (e) => {
        const list = e.payload.items.map((i) => i.content).join(", ");
        setTurns((prev) => [
          ...prev,
          { role: "system", text: `${e.payload.items.length} reminder(s) missed while closed: ${list}` },
        ]);
      },
    );
    return () => { unlisten.then((f) => f()); };
  }, []);

  useEffect(() => {
    const unlisten = listen("recall:reminder-failed", () => {
      setTurns((prev) => [
        ...prev,
        { role: "system", text: "A reminder failed to deliver. Check your Reminders tab." },
      ]);
    });
    return () => { unlisten.then((f) => f()); };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const handleStop = useCallback(async () => {
    let blob: Blob;
    try {
      blob = await recorder.stop();
    } catch (e) {
      console.error(e);
      setOrbError(true);
      setTimeout(() => { setOrbError(false); recorder.reset(); }, 2000);
      return;
    }

    const userTurnIdx = turns.length;
    const assistantTurnIdx = userTurnIdx + 1;

    setTurns((prev) => [
      ...prev,
      { role: "user", text: "…" },
      { role: "assistant", text: "", steps: [], pending: true },
    ]);

    try {
      let transcript = "";
      let spokenText = "";
      let intentType: string | undefined;
      let itemId: string | null = null;
      let dueAt: string | null = null;
      let audiob64 = "";

      recorder.setSpeaking();

      for await (const event of captureStream(blob, sessionId)) {
        if (event.type === "transcript") {
          transcript = event.text;
          setTurns((prev) => {
            const next = [...prev];
            next[userTurnIdx] = { ...next[userTurnIdx], text: transcript };
            return next;
          });

        } else if (event.type === "tool_call") {
          setTurns((prev) => {
            const next = [...prev];
            const asst = { ...next[assistantTurnIdx] };
            asst.steps = [...(asst.steps ?? []), { name: event.name, summary: "", pending: true }];
            next[assistantTurnIdx] = asst;
            return next;
          });

        } else if (event.type === "tool_result") {
          // Update matching pending step and extract card data
          setTurns((prev) => {
            const next = [...prev];
            const asst = { ...next[assistantTurnIdx] };
            const steps = [...(asst.steps ?? [])];
            // Find last pending step with this name
            const idx = [...steps].reverse().findIndex((s) => s.name === event.name && s.pending);
            if (idx !== -1) {
              const realIdx = steps.length - 1 - idx;
              steps[realIdx] = { ...steps[realIdx], summary: event.summary, pending: false };
            }
            asst.steps = steps;
            // surface_cards explicitly passes only the emails the agent discussed
            if (event.name === "surface_cards" && Array.isArray(event.data?.items_data)) {
              asst.emailCards = event.data.items_data as EmailCard[];
            }
            // surface_tasks explicitly passes only the tasks the agent discussed
            if (event.name === "surface_tasks" && Array.isArray(event.data?.items_data)) {
              asst.taskCards = event.data.items_data as TaskCard[];
            }
            next[assistantTurnIdx] = asst;
            return next;
          });

        } else if (event.type === "ack_audio") {
          playAudio(event.audio_base64);

        } else if (event.type === "spoken") {
          spokenText = event.text;
          setTurns((prev) => {
            const next = [...prev];
            next[assistantTurnIdx] = { ...next[assistantTurnIdx], text: spokenText };
            return next;
          });

        } else if (event.type === "metadata") {
          intentType = event.intent_type;
          setTurns((prev) => {
            const next = [...prev];
            next[userTurnIdx] = { ...next[userTurnIdx], intentType };
            return next;
          });

        } else if (event.type === "stored") {
          itemId = event.item_id;
          dueAt = event.due_at;

        } else if (event.type === "audio") {
          audiob64 = event.audio_base64;

        } else if (event.type === "done") {
          setTurns((prev) => {
            const next = [...prev];
            next[assistantTurnIdx] = { ...next[assistantTurnIdx], pending: false };
            return next;
          });
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
        }
      }
    } catch (e) {
      console.error(e);
      setTurns((prev) => {
        const next = [...prev];
        next[assistantTurnIdx] = { ...next[assistantTurnIdx], text: "Something went wrong.", pending: false };
        return next;
      });
      setOrbError(true);
      setTimeout(() => { setOrbError(false); recorder.reset(); }, 2000);
    }
  }, [recorder, sessionId, turns.length]);

  const lastToggleMs = useRef(0);
  const handleToggle = useCallback(() => {
    const now = Date.now();
    if (now - lastToggleMs.current < 600) return;
    lastToggleMs.current = now;
    if (recorder.state === "idle") {
      recorder.start().catch(console.error);
    } else if (recorder.state === "recording") {
      handleStop();
    }
  }, [recorder, handleStop]);

  const orbState = orbError ? "error" : recorder.state;

  return (
    <div className="agent-tab">
      <div className="agent-hint">
        Press <kbd>Ctrl+Shift+Space</kbd> or click the orb to start speaking
      </div>

      <div className="agent-turns">
        {turns.length === 0 && (
          <div className="agent-empty">
            <span className="agent-empty__icon">◈</span>
            <p>Your conversation will appear here.</p>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn turn--${t.role}${t.pending ? " turn--pending" : ""}`}>
            {t.intentType && t.role === "user" && (
              <span
                className="turn__badge"
                style={{ color: INTENT_COLORS[t.intentType] ?? "#aaa", borderColor: INTENT_COLORS[t.intentType] ?? "#aaa" }}
              >
                {t.intentType.replace("_", " ")}
              </span>
            )}
            {t.steps && t.steps.length > 0 && (
              <StepsGroup steps={t.steps} />
            )}
            {t.text && <p>{t.text}</p>}
            {t.emailCards && t.emailCards.length > 0 && (
              <EmailCardGrid cards={t.emailCards} />
            )}
            {t.taskCards && t.taskCards.length > 0 && (
              <TaskCardGrid cards={t.taskCards} />
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="agent-orb-row">
        <div className={`orb-core orb-core--${orbState}`} onClick={handleToggle}>
          <div className="orb-shimmer" />
          <div className="orb-specular" />
          <div className="orb-scanlines" />
        </div>
      </div>
    </div>
  );
}

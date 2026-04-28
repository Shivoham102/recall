import React, { useEffect, useRef, useState, useCallback } from "react";
import { listen } from "@tauri-apps/api/event";
import { emit } from "@tauri-apps/api/event";
import { captureStream, playAudio, getOrCreateSessionId } from "../../services/api";
import { useRecorder } from "../../hooks/useRecorder";
import { scheduleReminder } from "../../services/reminderScheduler";

interface AgentStep {
  name: string;
  summary: string;
  type: "tool_call" | "tool_result";
}

interface Turn {
  role: "user" | "assistant" | "system";
  text: string;
  intentType?: string;
  steps?: AgentStep[];
  pending?: boolean;
}

const INTENT_COLORS: Record<string, string> = {
  task:       "#00e5ff",
  blocker:    "#ff4466",
  follow_up:  "#ff9900",
  progress:   "#00ff88",
  note:       "#9988ff",
  query:      "#aaaaaa",
  update:     "#ffcc00",
};

const TOOL_LABELS: Record<string, string> = {
  recall_search:      "searching memory",
  recall_update_item: "updating item",
  file_create:        "creating file",
  gmail_draft:        "saving draft",
  gmail_send:         "sending email",
  calendar_list:      "checking calendar",
  calendar_create:    "creating event",
  classify_intent:    "classifying",
};

function AgentStepRow({ step }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false);
  const label = step.type === "tool_call"
    ? `▸ ${TOOL_LABELS[step.name] ?? step.name}`
    : `  ${step.summary}`;

  return (
    <div
      className="agent-step"
      onClick={() => setExpanded((e) => !e)}
      title={expanded ? "collapse" : "expand"}
    >
      <span className="agent-step__label">{label}</span>
      <span className="agent-step__tag">[{step.name}]</span>
    </div>
  );
}

export function AgentTab() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [orbError, setOrbError] = useState(false);
  const sessionId = useRef(getOrCreateSessionId()).current;
  const bottomRef = useRef<HTMLDivElement>(null);
  const recorder = useRecorder();

  useEffect(() => {
    const unlisten = listen<{ transcript: string; response_text: string; intent_type: string }>(
      "recall:new-turn",
      (e) => {
        // Only process events from OrbWindow (AgentTab generates its own turns via stream)
      },
    );
    return () => { unlisten.then((f) => f()); };
  }, []);

  useEffect(() => {
    const unlisten = listen<{ items: { id: string; content: string }[] }>(
      "recall:reminders-missed",
      (e) => {
        const list = e.payload.items.map((i) => i.content).join(", ");
        setTurns((prev) => [
          ...prev,
          { role: "system", text: `${e.payload.items.length} reminder(s) were missed while the app was closed: ${list}` },
        ]);
      },
    );
    return () => { unlisten.then((f) => f()); };
  }, []);

  useEffect(() => {
    const unlisten = listen("recall:reminder-failed", () => {
      setTurns((prev) => [
        ...prev,
        { role: "system", text: "A reminder failed to deliver after 3 attempts. Check your Reminders tab." },
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

    // Add placeholder user turn immediately; assistant turn with pending state
    const userTurnIdx = turns.length;
    setTurns((prev) => [
      ...prev,
      { role: "user", text: "…" },
      { role: "assistant", text: "", steps: [], pending: true },
    ]);

    const assistantTurnIdx = userTurnIdx + 1;

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
            asst.steps = [...(asst.steps ?? []), { name: event.name, summary: "", type: "tool_call" }];
            next[assistantTurnIdx] = asst;
            return next;
          });
        } else if (event.type === "tool_result") {
          setTurns((prev) => {
            const next = [...prev];
            const asst = { ...next[assistantTurnIdx] };
            asst.steps = [...(asst.steps ?? []), { name: event.name, summary: event.summary, type: "tool_result" }];
            next[assistantTurnIdx] = asst;
            return next;
          });
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
        } else if (event.type === "ack_audio") {
          // Play immediately — user hears acknowledgment while tools are running
          playAudio(event.audio_base64);
        } else if (event.type === "audio") {
          audiob64 = event.audio_base64;
        } else if (event.type === "done") {
          // Finalize
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
          if (dueAt && itemId) {
            scheduleReminder(itemId, dueAt);
          }
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
              <div className="agent-steps">
                {t.steps.map((step, j) => (
                  <AgentStepRow key={j} step={step} />
                ))}
              </div>
            )}
            <p>{t.text || (t.pending ? "…" : "")}</p>
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

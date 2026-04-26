import React, { useEffect, useRef, useState, useCallback } from "react";
import { listen } from "@tauri-apps/api/event";
import { emit } from "@tauri-apps/api/event";
import { capture, playAudio, getOrCreateSessionId } from "../../services/api";
import { useRecorder } from "../../hooks/useRecorder";

interface Turn {
  role: "user" | "assistant";
  text: string;
  intentType?: string;
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

export function AgentTab() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [orbError, setOrbError] = useState(false);
  const sessionId = useRef(getOrCreateSessionId()).current;
  const bottomRef = useRef<HTMLDivElement>(null);
  const recorder = useRecorder();

  // Listen for voice turns from either orb (floating or embedded)
  useEffect(() => {
    const unlisten = listen<{ transcript: string; response_text: string; intent_type: string }>(
      "recall:new-turn",
      (e) => {
        setTurns((prev) => [
          ...prev,
          { role: "user",      text: e.payload.transcript,    intentType: e.payload.intent_type },
          { role: "assistant", text: e.payload.response_text },
        ]);
      },
    );
    return () => { unlisten.then((f) => f()); };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const handleStop = useCallback(async () => {
    try {
      const blob = await recorder.stop();
      const resp = await capture(blob, sessionId);
      recorder.setSpeaking();
      playAudio(resp.audio_base64, () => recorder.reset());
      await emit("recall:new-turn", {
        transcript: resp.transcript,
        response_text: resp.response_text,
        intent_type: resp.intent_type,
        item_id: resp.item_id,
      });
    } catch (e) {
      console.error(e);
      setOrbError(true);
      setTimeout(() => { setOrbError(false); recorder.reset(); }, 2000);
    }
  }, [recorder, sessionId]);

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
          <div key={i} className={`turn turn--${t.role}`}>
            {t.intentType && t.role === "user" && (
              <span
                className="turn__badge"
                style={{ color: INTENT_COLORS[t.intentType] ?? "#aaa", borderColor: INTENT_COLORS[t.intentType] ?? "#aaa" }}
              >
                {t.intentType.replace("_", " ")}
              </span>
            )}
            <p>{t.text}</p>
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

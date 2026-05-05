import React, { useEffect, useRef, useState, useCallback } from "react";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useRecorder } from "../hooks/useRecorder";
import { VoiceButton } from "./VoiceButton";
import { ChatHistory, Turn } from "./ChatHistory";
import { captureStream, playAudio } from "../services/api";

const HOTKEY = "Ctrl+Shift+Space";

function getOrCreateSessionId(): string {
  let id = localStorage.getItem("recall_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("recall_session_id", id);
  }
  return id;
}

export function FloatingWindow() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sessionId] = useState(getOrCreateSessionId);
  const recorder = useRecorder();
  const appWindow = useRef(getCurrentWindow()).current;

  useEffect(() => {
    const setup = async () => {
      await unregister(HOTKEY).catch(() => {});
      await register(HOTKEY, async () => {
        const visible = await appWindow.isVisible();
        if (visible) {
          await appWindow.hide();
        } else {
          await appWindow.show();
          await appWindow.setFocus();
        }
      });
    };

    setup().catch(console.error);

    return () => {
      unregister(HOTKEY).catch(() => {});
    };
  }, []);

  const handleStop = useCallback(async () => {
    let blob: Blob;
    try {
      blob = await recorder.stop();
    } catch (e) {
      setError(String(e));
      recorder.reset();
      return;
    }

    const userTurnIdx = turns.length;
    setTurns((prev) => [...prev, { role: "user", text: "…" }]);

    try {
      let spokenText = "";
      let audiob64 = "";

      recorder.setSpeaking();

      for await (const event of captureStream(blob, sessionId)) {
        if (event.type === "transcript") {
          setTurns((prev) => {
            const next = [...prev];
            next[userTurnIdx] = { ...next[userTurnIdx], text: event.text };
            return next;
          });
        } else if (event.type === "metadata") {
          setTurns((prev) => {
            const next = [...prev];
            next[userTurnIdx] = { ...next[userTurnIdx], intentType: event.intent_type };
            return next;
          });
        } else if (event.type === "ack_audio") {
          playAudio(event.audio_base64);
        } else if (event.type === "spoken") {
          spokenText = event.text;
        } else if (event.type === "audio") {
          audiob64 = event.audio_base64;
        } else if (event.type === "done") {
          setTurns((prev) => [...prev, { role: "assistant", text: spokenText }]);
          if (audiob64) {
            playAudio(audiob64, () => recorder.reset());
          } else {
            recorder.reset();
          }
        }
      }
    } catch (e) {
      setError(String(e));
      recorder.reset();
    }
  }, [recorder, sessionId, turns.length]);

  return (
    <div className="floating-window">
      <div className="floating-window__header">
        <span className="floating-window__title" data-tauri-drag-region>Recall</span>
        <button
          className="floating-window__close"
          onClick={() => appWindow.hide()}
          title="Hide (or use Ctrl+Shift+Space)"
        >
          ✕
        </button>
      </div>

      {error && (
        <div className="error-banner" onClick={() => setError(null)} title="Click to dismiss">
          {error}
        </div>
      )}

      <ChatHistory turns={turns} />

      <div className="floating-window__footer">
        <VoiceButton
          state={recorder.state}
          onStart={recorder.start}
          onStop={handleStop}
        />
      </div>
    </div>
  );
}

import { useEffect, useRef, useCallback, useState } from "react";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useRecorder } from "../hooks/useRecorder";
import { captureStream, playAudio, getOrCreateSessionId } from "../services/api";
import { scheduleReminder } from "../services/reminderScheduler";
import { applyItemUpdatedTimer } from "../services/itemUpdatedTimer";

const HOTKEY = "Ctrl+Shift+Space";

export function OrbWindow() {
  const recorder = useRecorder();
  const sessionId = useRef(getOrCreateSessionId()).current;
  const [error, setError] = useState(false);
  const appWindow = useRef(getCurrentWindow()).current;
  const handleToggleRef = useRef<() => void>(() => {});
  const lastToggleMs = useRef(0);

  const handleStop = useCallback(async () => {
    try {
      const blob = await recorder.stop();
      // Dedicated persistent voice session, intentionally isolated from AgentTab chats
      let transcript = "", responseText = "", intentType = "";
      let awaitingClarification = false;
      let itemId: string | null = null, dueAt: string | null = null, audiob64 = "";

      for await (const event of captureStream(blob, sessionId)) {
        if (event.type === "transcript")     transcript = event.text;
        else if (event.type === "ack_audio") playAudio(event.audio_base64);
        else if (event.type === "spoken")    responseText = event.text;
        else if (event.type === "metadata") {
          intentType = event.intent_type;
          awaitingClarification = event.awaiting_clarification;
        }
        else if (event.type === "stored")  { itemId = event.item_id; dueAt = event.due_at; }
        else if (event.type === "item_updated") applyItemUpdatedTimer(event.item_id, event.due_at, "OrbWindow");
        else if (event.type === "audio")     audiob64 = event.audio_base64;
        else if (event.type === "error")     throw new Error(event.message);
        else if (event.type === "done")      break;
      }

      if (awaitingClarification) {
        if (audiob64) {
          playAudio(audiob64, () => { recorder.reset(); recorder.start().catch(() => {}); });
        } else {
          recorder.reset();
          recorder.start().catch(() => {});
        }
      } else {
        if (audiob64) {
          recorder.setSpeaking();
          playAudio(audiob64, async () => { recorder.reset(); await appWindow.hide(); });
        } else {
          recorder.reset();
          await appWindow.hide();
        }
        if (dueAt && itemId) scheduleReminder(itemId, dueAt);
      }

      await emit("recall:new-turn", {
        transcript,
        response_text: responseText,
        intent_type: intentType,
        item_id: itemId,
      });
    } catch (e) {
      console.error(e);
      setError(true);
      setTimeout(async () => {
        setError(false);
        recorder.reset();
        await appWindow.hide();
      }, 2000);
    }
  }, [appWindow, recorder, sessionId]);

  const handleToggle = useCallback(() => {
    const now = Date.now();
    if (now - lastToggleMs.current < 600) return;
    lastToggleMs.current = now;

    if (recorder.state === "idle") {
      appWindow.show().catch(() => {});
      recorder.start().catch(() => {});
    } else {
      appWindow.hide().catch(() => {});
      if (recorder.state === "recording") {
        void handleStop();
      }
    }
  }, [appWindow, handleStop, recorder]);

  useEffect(() => {
    handleToggleRef.current = handleToggle;
  }, [handleToggle]);

  useEffect(() => {
    const setup = async () => {
      await unregister(HOTKEY).catch(() => {});
      await register(HOTKEY, () => handleToggleRef.current());
    };
    setup().catch(() => {});
    return () => { unregister(HOTKEY).catch(() => {}); };
  }, []);

  useEffect(() => {
    const unlisten = listen<{ audio_base64: string }>("recall:reminder", async (e) => {
      if (recorder.state !== "idle") return;
      recorder.setSpeaking();
      await appWindow.show();
      playAudio(e.payload.audio_base64, async () => {
        recorder.reset();
        await appWindow.hide();
      });
    });
    return () => { unlisten.then((f) => f()); };
  }, [recorder, appWindow]);

  const orbState = error ? "error" : recorder.state;

  return (
    <div className="orb-root">
      <div className={`orb-glow orb-glow--${orbState}`} />
      <div className={`orb-core orb-core--${orbState}`} onClick={handleToggle}>
        <div className="orb-shimmer" />
        <div className="orb-specular" />
        <div className="orb-scanlines" />
      </div>
    </div>
  );
}

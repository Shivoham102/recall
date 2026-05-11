import { useEffect, useRef, useCallback, useState } from "react";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useRecorder } from "../hooks/useRecorder";
import { capture, playAudio, getOrCreateSessionId } from "../services/api";
import { scheduleReminder } from "../services/reminderScheduler";

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
      const resp = await capture(blob, sessionId);

      recorder.setSpeaking();
      playAudio(resp.audio_base64, async () => {
        recorder.reset();
        await appWindow.hide();
      });

      if (resp.due_at && resp.item_id) {
        scheduleReminder(resp.item_id, resp.due_at);
      }

      await emit("recall:new-turn", {
        transcript: resp.transcript,
        response_text: resp.response_text,
        intent_type: resp.intent_type,
        item_id: resp.item_id,
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

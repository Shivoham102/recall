import React, { useEffect, useRef, useCallback, useState } from "react";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { emit } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useRecorder } from "../hooks/useRecorder";
import { capture, playAudio, getOrCreateSessionId } from "../services/api";

const HOTKEY = "Ctrl+Shift+Space";

export function OrbWindow() {
  const recorder = useRecorder();
  const sessionId = useRef(getOrCreateSessionId()).current;
  const [error, setError] = useState(false);
  const appWindow = useRef(getCurrentWindow()).current;

  // Keep a stable ref to the toggle so the hotkey handler never goes stale
  const handleToggleRef = useRef<() => void>(() => {});

  const handleStop = useCallback(async () => {
    try {
      const blob = await recorder.stop();
      const resp = await capture(blob, sessionId);

      recorder.setSpeaking();
      playAudio(resp.audio_base64, async () => {
        recorder.reset();
        await appWindow.hide();
      });

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
  }, [recorder, sessionId, appWindow]);

  const handleToggle = useCallback(() => {
    if (recorder.state === "idle") {
      appWindow.show()
        .then(() => appWindow.setFocus())
        .then(() => recorder.start())
        .catch(console.error);
    } else {
      // Hide immediately on any subsequent press; audio processing continues in background
      appWindow.hide().catch(console.error);
      if (recorder.state === "recording") {
        handleStop();
      }
    }
  }, [recorder, handleStop, appWindow]);

  useEffect(() => {
    handleToggleRef.current = handleToggle;
  }, [handleToggle]);

  useEffect(() => {
    const setup = async () => {
      await unregister(HOTKEY).catch(() => {});
      await register(HOTKEY, () => handleToggleRef.current());
    };
    setup().catch(console.error);
    return () => { unregister(HOTKEY).catch(() => {}); };
  }, []);

  const orbState = error ? "error" : recorder.state;

  return (
    <div className="orb-root">
      <div className={`orb-core orb-core--${orbState}`} onClick={handleToggle}>
        <div className="orb-shimmer" />
        <div className="orb-specular" />
        <div className="orb-scanlines" />
      </div>
    </div>
  );
}

import { useEffect, useRef, useCallback, useState } from "react";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow, Window } from "@tauri-apps/api/window";
import { useRecorder } from "../hooks/useRecorder";
import { captureStream, playAudio, getOrCreateSessionId } from "../services/api";
import { StreamingAudioPlayer } from "../services/audioPlayer";
import { scheduleReminder } from "../services/reminderScheduler";
import { applyItemUpdatedTimer } from "../services/itemUpdatedTimer";

const HOTKEY = "Ctrl+Shift+Space";
type ProactivePlaybackStatus = "started" | "skipped_busy" | "rejected" | "error";

async function isMainForeground(): Promise<boolean> {
  try {
    const mainWin = await Window.getByLabel("main");
    if (!mainWin) return false;
    const [visible, minimized, focused] = await Promise.all([
      mainWin.isVisible(),
      mainWin.isMinimized(),
      mainWin.isFocused(),
    ]);
    return visible && !minimized && focused;
  } catch {
    return false;
  }
}

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
      let itemId: string | null = null, dueAt: string | null = null;
      let ackPlayer: StreamingAudioPlayer | null = null;
      let finalPlayer: StreamingAudioPlayer | null = null;
      let hasFinalAudio = false;

      for await (const event of captureStream(blob, sessionId)) {
        if (event.type === "transcript") {
          transcript = event.text;
        } else if (event.type === "ack_audio") {
          playAudio(event.audio_base64);
        } else if (event.type === "ack_audio_chunk") {
          if (!ackPlayer) ackPlayer = new StreamingAudioPlayer();
          ackPlayer.append(event.data);
        } else if (event.type === "ack_audio_done") {
          ackPlayer?.done();
          ackPlayer = null;
        } else if (event.type === "spoken") {
          responseText = event.text;
        } else if (event.type === "metadata") {
          intentType = event.intent_type;
          awaitingClarification = event.awaiting_clarification;
        } else if (event.type === "stored") {
          itemId = event.item_id;
          dueAt = event.due_at;
        } else if (event.type === "item_updated") {
          applyItemUpdatedTimer(event.item_id, event.due_at, "OrbWindow");
        } else if (event.type === "audio") {
          hasFinalAudio = true;
          if (!awaitingClarification) recorder.setSpeaking();
          playAudio(event.audio_base64, async () => {
            recorder.reset();
            if (awaitingClarification) recorder.start().catch(() => {});
            else await appWindow.hide();
          });
        } else if (event.type === "audio_chunk") {
          hasFinalAudio = true;
          if (!finalPlayer) {
            if (!awaitingClarification) recorder.setSpeaking();
            finalPlayer = new StreamingAudioPlayer(async () => {
              recorder.reset();
              if (awaitingClarification) recorder.start().catch(() => {});
              else await appWindow.hide();
            });
          }
          finalPlayer.append(event.data);
        } else if (event.type === "audio_done") {
          finalPlayer?.done();
          finalPlayer = null;
        } else if (event.type === "error") {
          throw new Error(event.message);
        } else if (event.type === "done") {
          break;
        }
      }

      if (!hasFinalAudio) {
        if (awaitingClarification) {
          recorder.reset();
          recorder.start().catch(() => {});
        } else {
          recorder.reset();
          await appWindow.hide();
        }
      }

      if (!awaitingClarification && dueAt && itemId) scheduleReminder(itemId, dueAt);

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

  const playProactiveAudio = useCallback(async (b64: string): Promise<ProactivePlaybackStatus> => {
    if (recorder.state !== "idle") return "skipped_busy";
    recorder.setSpeaking();
    const foreground = await isMainForeground();
    if (!foreground) await appWindow.show();
    const playback = playAudio(b64, async () => {
      recorder.reset();
      if (!foreground) await appWindow.hide();
    });
    const startStatus = await playback.started;
    if (startStatus === "started") return "started";

    recorder.reset();
    if (!foreground) await appWindow.hide().catch(() => {});
    return startStatus === "rejected" ? "rejected" : "error";
  }, [recorder, appWindow]);

  useEffect(() => {
    const unlisten = listen<{ audio_base64: string }>("recall:reminder", async (e) => {
      await playProactiveAudio(e.payload.audio_base64);
    });
    return () => { unlisten.then((f) => f()); };
  }, [playProactiveAudio]);

  useEffect(() => {
    const unlisten = listen<{ job_id?: string; audio_b64: string }>("recall:proactive-ready", async (e) => {
      const status = await playProactiveAudio(e.payload.audio_b64);
      if (e.payload.job_id) {
        await emit("recall:proactive-ready-status", { job_id: e.payload.job_id, status }).catch(() => {});
      }
    });
    return () => { unlisten.then((f) => f()); };
  }, [playProactiveAudio]);

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

import { useEffect, useRef, useCallback, useState } from "react";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow, Window } from "@tauri-apps/api/window";
import { useRecorder } from "../hooks/useRecorder";
import { captureStream, playAudio, stopCurrentAudio, getOrCreateSessionId, ensureHotkeyChat, persistHotkeyChat, StreamEvent } from "../services/api";
import { StreamingAudioPlayer } from "../services/audioPlayer";
import { scheduleReminder } from "../services/reminderScheduler";
import { applyItemUpdatedTimer } from "../services/itemUpdatedTimer";
import { AgentTurn } from "../types/agentTurn";
import { CapturePair, reduceCapturePair } from "../utils/captureTurnReducer";
import { Orb } from "./Orb/OrbCanvas";

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
  // Barge-in: refs let handleToggle abort the in-flight request + audio so it can
  // start a new recording while the agent is thinking/speaking. requestGenRef is
  // bumped on every new recording/barge-in; a stale handleStop (or proactive
  // playback) whose captured gen no longer matches skips all terminal actions
  // (reset/hide/emit), so it can't stomp the new request. This is robust against
  // the async path where captureStream aborts cleanly and the for-await just ends.
  const currentStreamAbortRef = useRef<AbortController | null>(null);
  const currentAckPlayerRef = useRef<StreamingAudioPlayer | null>(null);
  const currentFinalPlayerRef = useRef<StreamingAudioPlayer | null>(null);
  const requestGenRef = useRef(0);

  // ── Hotkey chat producer ───────────────────────────────────────────────────
  // The orb is the sole writer of the single dedicated "Hotkey" chat: it reduces
  // each capture into a turn pair, emits live Tauri updates for the main window to
  // mirror, and persists the running turns to Supabase. hotkeyChatRef/turnsRef live
  // for the whole window session; the pair refs are per-turn.
  const hotkeyChatRef = useRef<{ id: string; user_id: string; agent_session_id: string } | null>(null);
  const hotkeyTurnsRef = useRef<AgentTurn[]>([]);
  const hotkeyPairRef = useRef<CapturePair | null>(null);
  const lastHotkeyPairRef = useRef<CapturePair | null>(null); // survives finalize → backs catchup
  const hotkeyPersistTimer = useRef<number | null>(null);
  const hotkeyEmitTimer = useRef<number | null>(null);
  const hotkeyEmitDirty = useRef(false);

  const flushHotkeyPersist = useCallback(async () => {
    if (hotkeyPersistTimer.current !== null) {
      window.clearTimeout(hotkeyPersistTimer.current);
      hotkeyPersistTimer.current = null;
    }
    const chat = hotkeyChatRef.current;
    if (!chat) return;
    await persistHotkeyChat({ ...chat, turns: hotkeyTurnsRef.current });
  }, []);

  const scheduleHotkeyPersist = useCallback(() => {
    if (hotkeyPersistTimer.current !== null) return;
    hotkeyPersistTimer.current = window.setTimeout(() => {
      hotkeyPersistTimer.current = null;
      void flushHotkeyPersist();
    }, 600);
  }, [flushHotkeyPersist]);

  const emitHotkeyPair = useCallback(() => {
    const chat = hotkeyChatRef.current;
    const pair = hotkeyPairRef.current;
    if (!chat || !pair) return;
    void emit("recall:hotkey-update", { chatId: chat.id, turns: [pair.user, pair.assistant] });
  }, []);

  // Emit the current pair. Tokens coalesce to ~100ms; structural events go immediately.
  const pushHotkeyEmit = useCallback((immediate: boolean) => {
    if (immediate) {
      hotkeyEmitDirty.current = false;
      if (hotkeyEmitTimer.current !== null) { window.clearTimeout(hotkeyEmitTimer.current); hotkeyEmitTimer.current = null; }
      emitHotkeyPair();
      return;
    }
    if (hotkeyEmitTimer.current !== null) { hotkeyEmitDirty.current = true; return; }
    emitHotkeyPair();
    hotkeyEmitTimer.current = window.setTimeout(() => {
      hotkeyEmitTimer.current = null;
      if (hotkeyEmitDirty.current) { hotkeyEmitDirty.current = false; emitHotkeyPair(); }
    }, 100);
  }, [emitHotkeyPair]);

  // Write the current pair back into the running turns array (replace the two by id).
  const syncHotkeyTurns = useCallback(() => {
    const pair = hotkeyPairRef.current;
    if (!pair) return;
    hotkeyTurnsRef.current = hotkeyTurnsRef.current.map((t) =>
      t.id === pair.user.id ? pair.user : t.id === pair.assistant.id ? pair.assistant : t,
    );
  }, []);

  // Begin a fresh hotkey turn pair. Ensures the chat (lazily, once per window session)
  // and emits the full row so main can insert it if it hasn't loaded the chat yet.
  // Returns false when logged out / persistence unavailable.
  const startHotkeyTurn = useCallback(async (): Promise<boolean> => {
    hotkeyPairRef.current = null;
    try {
      if (!hotkeyChatRef.current) {
        const ensured = await ensureHotkeyChat(sessionId);
        if (!ensured) return false;
        hotkeyChatRef.current = { id: ensured.id, user_id: ensured.user_id, agent_session_id: ensured.agent_session_id };
        hotkeyTurnsRef.current = [...ensured.turns];
      }
      const chat = hotkeyChatRef.current;
      const pair: CapturePair = {
        user: { id: crypto.randomUUID(), role: "user", text: "", pending: true },
        assistant: { id: crypto.randomUUID(), role: "assistant", text: "", steps: [], pending: true },
      };
      hotkeyPairRef.current = pair;
      lastHotkeyPairRef.current = pair;
      hotkeyTurnsRef.current = [...hotkeyTurnsRef.current, pair.user, pair.assistant];
      const ts = new Date().toISOString();
      void emit("recall:hotkey-start", {
        chat: {
          id: chat.id,
          user_id: chat.user_id,
          agent_session_id: chat.agent_session_id,
          title: "Hotkey",
          turns: hotkeyTurnsRef.current,
          last_capture: null,
          archived_at: null,
          created_at: ts,
          updated_at: ts,
          is_hotkey: true,
        },
      });
      scheduleHotkeyPersist();
      return true;
    } catch (e) {
      console.warn("[hotkey] start failed:", e);
      return false;
    }
  }, [sessionId, scheduleHotkeyPersist]);

  // Reduce one stream event into the current pair, then emit + persist.
  const reduceHotkey = useCallback((event: StreamEvent) => {
    const pair = hotkeyPairRef.current;
    if (!pair || !hotkeyChatRef.current) return;
    if (event.type === "done" || event.type === "error") {
      const assistant: AgentTurn = {
        ...pair.assistant,
        pending: false,
        ...(event.type === "error" ? { text: event.message } : {}),
      };
      const next = { ...pair, assistant };
      hotkeyPairRef.current = next;
      lastHotkeyPairRef.current = next;
      syncHotkeyTurns();
      pushHotkeyEmit(true);
      void flushHotkeyPersist();
      return;
    }
    const shape =
      event.type === "transcript" || event.type === "token" || event.type === "tool_call" ||
      event.type === "tool_result" || event.type === "spoken" || event.type === "metadata" || event.type === "stored";
    if (!shape) return;
    const next = reduceCapturePair(pair, event);
    hotkeyPairRef.current = next;
    lastHotkeyPairRef.current = next;
    syncHotkeyTurns();
    pushHotkeyEmit(event.type !== "token");
    scheduleHotkeyPersist();
  }, [syncHotkeyTurns, pushHotkeyEmit, scheduleHotkeyPersist, flushHotkeyPersist]);

  // Barge-in cut the current turn: resolve it (drop "working...") and flush.
  const finalizeHotkeyForBargeIn = useCallback(() => {
    const pair = hotkeyPairRef.current;
    const chat = hotkeyChatRef.current;
    if (!pair || !chat) return;
    const next = { ...pair, assistant: { ...pair.assistant, pending: false } };
    hotkeyPairRef.current = null;
    lastHotkeyPairRef.current = next;
    hotkeyTurnsRef.current = hotkeyTurnsRef.current.map((t) =>
      t.id === next.user.id ? next.user : t.id === next.assistant.id ? next.assistant : t,
    );
    void emit("recall:hotkey-update", { chatId: chat.id, turns: [next.user, next.assistant] });
    void flushHotkeyPersist();
  }, [flushHotkeyPersist]);

  const handleStop = useCallback(async () => {
    // Claim this request's generation. If a barge-in bumps requestGenRef, isCurrent()
    // goes false and every terminal action below is skipped so we don't stomp the
    // new recording.
    const myGen = requestGenRef.current;
    const isCurrent = () => requestGenRef.current === myGen;
    currentAckPlayerRef.current = null;
    currentFinalPlayerRef.current = null;
    const abort = new AbortController();
    currentStreamAbortRef.current = abort;
    try {
      const blob = await recorder.stop();
      // Dedicated persistent voice session, intentionally isolated from AgentTab chats
      let transcript = "", responseText = "", intentType = "";
      let awaitingClarification = false;
      let itemId: string | null = null, dueAt: string | null = null;
      let ackPlayer: StreamingAudioPlayer | null = null;
      let finalPlayer: StreamingAudioPlayer | null = null;
      let hasFinalAudio = false;

      // Begin the live Hotkey-chat turn (background; never blocks the audio path).
      const hotkeyEnabled = await startHotkeyTurn();

      for await (const event of captureStream(blob, sessionId, abort.signal)) {
        // Mirror the turn into the Hotkey chat. Gate on isCurrent() so a superseded
        // (barged-in) loop can't bleed events into the new pair.
        if (hotkeyEnabled && isCurrent()) reduceHotkey(event);
        if (event.type === "transcript") {
          transcript = event.text;
        } else if (event.type === "ack_audio") {
          playAudio(event.audio_base64);
        } else if (event.type === "ack_audio_chunk") {
          if (!ackPlayer) { ackPlayer = new StreamingAudioPlayer(); currentAckPlayerRef.current = ackPlayer; }
          ackPlayer.append(event.data);
        } else if (event.type === "ack_audio_done") {
          ackPlayer?.done();
          ackPlayer = null;
          currentAckPlayerRef.current = null;
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
            if (!isCurrent()) return;  // barged in — leave new recording alone
            recorder.reset();
            if (awaitingClarification) recorder.start().catch(() => {});
            else await appWindow.hide();
          });
        } else if (event.type === "audio_chunk") {
          hasFinalAudio = true;
          if (!finalPlayer) {
            if (!awaitingClarification) recorder.setSpeaking();
            finalPlayer = new StreamingAudioPlayer(async () => {
              if (!isCurrent()) return;  // barged in — leave new recording alone
              recorder.reset();
              if (awaitingClarification) recorder.start().catch(() => {});
              else await appWindow.hide();
            });
            currentFinalPlayerRef.current = finalPlayer;
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

      // captureStream may have ended because a barge-in aborted it — bail before any
      // state-mutating cleanup so we don't disturb the new recording.
      if (!isCurrent()) return;

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
      // Barge-in aborted the in-flight fetch, or a newer request superseded us.
      if ((e as DOMException)?.name === "AbortError" || !isCurrent()) return;
      console.error(e);
      setError(true);
      setTimeout(async () => {
        if (!isCurrent()) return;
        setError(false);
        recorder.reset();
        await appWindow.hide();
      }, 2000);
    }
  }, [appWindow, recorder, sessionId, startHotkeyTurn, reduceHotkey]);

  const handleToggle = useCallback(() => {
    const now = Date.now();
    if (now - lastToggleMs.current < 600) return;
    lastToggleMs.current = now;

    if (recorder.state === "idle") {
      requestGenRef.current++;
      appWindow.show().catch(() => {});
      recorder.start().catch(() => {});
    } else if (recorder.state === "recording") {
      // Stop recording but KEEP the orb visible so it can show the thinking
      // (processing) and speaking animations — the user needs to see where the
      // audio is coming from. handleStop hides the window once the response ends
      // (or re-arms recording if a clarification is needed).
      void handleStop();
    } else {
      // processing/speaking → barge-in: the hotkey always means "talk". Cut the
      // current audio + cancel the in-flight request, then start a new recording.
      // Bumping the gen invalidates the old handleStop / proactive playback so they
      // won't reset or hide the orb out from under the new recording.
      requestGenRef.current++;
      finalizeHotkeyForBargeIn();                // resolve the interrupted Hotkey turn
      currentStreamAbortRef.current?.abort();   // → backend GeneratorExit → cancels tts_task
      currentFinalPlayerRef.current?.abort();   // stop streaming response audio
      currentAckPlayerRef.current?.abort();     // stop ack audio
      stopCurrentAudio();                        // stop any playAudio clip (proactive / legacy)
      recorder.reset();
      appWindow.show().catch(() => {});
      recorder.start().catch(() => {});
    }
  }, [appWindow, handleStop, recorder, finalizeHotkeyForBargeIn]);

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

  // Main window opened mid-turn (or just after) and asked for the latest state:
  // re-send the last pair, whether it's still in flight or already finalized.
  useEffect(() => {
    const unlisten = listen("recall:hotkey-catchup", () => {
      const pair = lastHotkeyPairRef.current;
      const chat = hotkeyChatRef.current;
      if (pair && chat) void emit("recall:hotkey-update", { chatId: chat.id, turns: [pair.user, pair.assistant] });
    });
    return () => { unlisten.then((f) => f()); };
  }, []);

  const playProactiveAudio = useCallback(async (b64: string): Promise<ProactivePlaybackStatus> => {
    if (recorder.state !== "idle") return "skipped_busy";
    const myGen = requestGenRef.current;
    const isCurrent = () => requestGenRef.current === myGen;
    recorder.setSpeaking();
    const foreground = await isMainForeground();
    if (!foreground) await appWindow.show();
    const playback = playAudio(b64, async () => {
      if (!isCurrent()) return;  // barged in mid-announcement — don't reset/hide
      recorder.reset();
      if (!foreground) await appWindow.hide();
    });
    const startStatus = await playback.started;
    if (startStatus === "started") return "started";

    if (!isCurrent()) return "rejected";
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
      <Orb state={orbState} size={88} onClick={handleToggle} />
    </div>
  );
}

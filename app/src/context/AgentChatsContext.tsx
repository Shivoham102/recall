import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { emit, listen } from "@tauri-apps/api/event";
import { supabase } from "../services/supabase";
import { ackProactiveJob, connectProactiveStream, fetchProactiveAnnounceAudio, playAudio, suggestAgentChatTitle } from "../services/api";
import { bumpSuppressed, isQuietContext } from "../services/quietContext";
import { showQuietCard } from "../services/notify";
import { addNotification, proactiveLabel } from "../services/notifications";
import { AgentChat, AgentTurn, CalendarCard, EmailCard, TaskCard } from "../types/agentTurn";
import { firstUserMessageText, normalizeStoredAgentChat } from "../utils/agentChatDisplay";

const PAGE_SIZE = 50;
const SAVE_DEBOUNCE_MS = 500;
const ACTIVE_CHAT_KEY = "recall_active_chat_id";
const LEGACY_SESSION_KEY = "recall_session_id";
const MORNING_BRIEF_MAX_AGE_MS = 5 * 60 * 1000;
const ORB_PLAYBACK_TIMEOUT_MS = 6000;

// Job types that render directly in the UI — only these trigger the unread dot.
// follow_up_scan and follow_up_draft are always filtered by coalesceProactiveTurns.
const PROACTIVE_UNREAD_TYPES = new Set(["morning_brief", "email_triage", "pattern_learn"]);

type OrbPlaybackStatus = "started" | "skipped_busy" | "rejected" | "error";

interface Cursor {
  updated_at: string;
  id: string;
}

interface AgentChatsContextValue {
  chats: AgentChat[];
  activeChat: AgentChat | null;
  activeChatId: string | null;
  loading: boolean;
  loadError: string | null;
  saveError: string | null;
  sidebarPinned: boolean;
  setSidebarPinned: (next: boolean) => void;
  setActiveChatId: (chatId: string) => void;
  createChat: (opts?: { sessionId?: string; activate?: boolean }) => AgentChat;
  loadMore: () => Promise<void>;
  hasMore: boolean;
  archiveChat: (chatId: string) => Promise<void>;
  renameChat: (chatId: string, title: string) => Promise<void>;
  deleteChat: (chatId: string) => Promise<void>;
  replaceChatTurns: (chatId: string, updater: (prev: AgentTurn[]) => AgentTurn[]) => void;
  patchTurnInChat: (chatId: string, turnId: string, patch: Partial<AgentTurn>) => void;
  flushNow: () => Promise<void>;
  /** LLM sidebar title from the first user message only; once per chat unless title is null. */
  refreshChatTitleFromServer: (chatId: string) => Promise<void>;
  proactiveUnread: boolean;
  clearProactiveUnread: () => void;
}

const AgentChatsContext = createContext<AgentChatsContextValue | null>(null);

function nowIso() {
  return new Date().toISOString();
}

interface ProviderProps {
  userId: string;
  children: React.ReactNode;
}

export function AgentChatsProvider({ userId, children }: ProviderProps) {
  const [chats, setChats] = useState<AgentChat[]>([]);
  const [activeChatId, setActiveChatIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [sidebarPinned, setSidebarPinnedState] = useState<boolean>(() => {
    try { return localStorage.getItem("recall_sidebar_pinned") === "1"; } catch { return false; }
  });
  const setSidebarPinned = (next: boolean) => {
    try { localStorage.setItem("recall_sidebar_pinned", next ? "1" : "0"); } catch { /* ignore */ }
    setSidebarPinnedState(next);
  };

  const [proactiveUnread, setProactiveUnread] = useState<boolean>(() => {
    try { return localStorage.getItem("recall_proactive_unread") === "1"; } catch { return false; }
  });
  const proactiveChatIdRef = useRef<string | null>(null);

  const chatsRef = useRef<AgentChat[]>([]);
  /** Chats the user explicitly renamed in this app session — do not overwrite with auto titles. */
  const titleLockedIdsRef = useRef<Set<string>>(new Set());
  const titleGenerationInFlightRef = useRef<Set<string>>(new Set());
  const dirtyIdsRef = useRef<Set<string>>(new Set());
  const flushTimerRef = useRef<number | null>(null);
  const appWindow = useRef(getCurrentWindow()).current;

  // True only when the main window is actually in the foreground (visible, not
  // minimized, AND focused). Mirrors OrbWindow's isMainForeground so the morning
  // brief follows the same orb rule as reminders: if the user isn't looking at the
  // app (unfocused or minimized to tray), route the announcement to the orb.
  const isMainForeground = useCallback(async () => {
    try {
      const [visible, minimized, focused] = await Promise.all([
        appWindow.isVisible(),
        appWindow.isMinimized(),
        appWindow.isFocused(),
      ]);
      return visible && !minimized && focused;
    } catch (err) {
      console.warn("[proactive] failed to read main window state:", err);
      return false;
    }
  }, [appWindow]);

  const requestOrbPlayback = useCallback((jobId: string, audioB64: string) => {
    return new Promise<OrbPlaybackStatus | "timeout" | "emit_error">((resolve) => {
      let settled = false;
      let timeoutId: number | null = null;
      let unlistenFn: (() => void) | null = null;

      const finish = (status: OrbPlaybackStatus | "timeout" | "emit_error") => {
        if (settled) return;
        settled = true;
        if (timeoutId !== null) window.clearTimeout(timeoutId);
        if (unlistenFn) unlistenFn();
        resolve(status);
      };

      void listen<{ job_id: string; status: OrbPlaybackStatus }>("recall:proactive-ready-status", (evt) => {
        if (evt.payload.job_id !== jobId) return;
        finish(evt.payload.status);
      })
        .then((unlisten) => {
          unlistenFn = unlisten;
          timeoutId = window.setTimeout(() => finish("timeout"), ORB_PLAYBACK_TIMEOUT_MS);
          void emit("recall:proactive-ready", { job_id: jobId, audio_b64: audioB64 })
            .catch((err) => {
              console.warn("[proactive] failed to emit orb playback request:", err);
              finish("emit_error");
            });
        })
        .catch((err) => {
          console.warn("[proactive] failed to listen for orb playback status:", err);
          finish("emit_error");
        });
    });
  }, []);

  useEffect(() => {
    chatsRef.current = chats;
  }, [chats]);

  const markDirty = useCallback((chatId: string) => {
    dirtyIdsRef.current.add(chatId);
    if (flushTimerRef.current !== null) return;
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null;
      void flushNow();
    }, SAVE_DEBOUNCE_MS);
  }, []);

  const flushNow = useCallback(async () => {
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const dirtyIds = Array.from(dirtyIdsRef.current);
    if (dirtyIds.length === 0) return;

    const nextDirty = new Set<string>();
    setSaveError(null);

    for (const chatId of dirtyIds) {
      const chat = chatsRef.current.find((c) => c.id === chatId);
      if (!chat || chat.archived_at) {
        dirtyIdsRef.current.delete(chatId);
        continue;
      }
      const payload = {
        id: chat.id,
        user_id: chat.user_id,
        agent_session_id: chat.agent_session_id,
        title: chat.title,
        turns: chat.turns,
        last_capture: chat.last_capture,
        archived_at: chat.archived_at,
        updated_at: chat.updated_at,
      };
      const { error } = await supabase
        .from("agent_chats")
        .upsert(payload, { onConflict: "id" });
      if (error) {
        nextDirty.add(chatId);
        setSaveError(error.message);
      }
    }
    dirtyIdsRef.current = nextDirty;
  }, []);

  const createChat = useCallback(
    (opts?: { sessionId?: string; activate?: boolean }) => {
      const ts = nowIso();
      const chat: AgentChat = {
        id: crypto.randomUUID(),
        user_id: userId,
        agent_session_id: opts?.sessionId ?? crypto.randomUUID(),
        title: null,
        turns: [],
        last_capture: null,
        archived_at: null,
        created_at: ts,
        updated_at: ts,
      };
      setChats((prev) => [chat, ...prev]);
      if (opts?.activate ?? true) {
        setActiveChatIdState(chat.id);
      }
      markDirty(chat.id);
      return chat;
    },
    [markDirty, userId],
  );

  const setActiveChatId = useCallback((chatId: string) => {
    setActiveChatIdState(chatId);
    localStorage.setItem(ACTIVE_CHAT_KEY, chatId);
  }, []);

  const replaceChatTurns = useCallback((chatId: string, updater: (prev: AgentTurn[]) => AgentTurn[]) => {
    setChats((prevChats) =>
      prevChats.map((chat) => {
        if (chat.id !== chatId) return chat;
        const nextTurns = updater(chat.turns);
        const nextUpdated = nowIso();
        markDirty(chat.id);
        return { ...chat, turns: nextTurns, updated_at: nextUpdated };
      }),
    );
  }, [markDirty]);

  const patchTurnInChat = useCallback((chatId: string, turnId: string, patch: Partial<AgentTurn>) => {
    replaceChatTurns(chatId, (prev) => prev.map((t) => (t.id === turnId ? { ...t, ...patch } : t)));
  }, [replaceChatTurns]);

  const archiveChat = useCallback(async (chatId: string) => {
    const ts = nowIso();
    setChats((prev) => prev.filter((c) => c.id !== chatId));
    if (activeChatId === chatId) {
      const fallback = chatsRef.current.find((c) => c.id !== chatId);
      setActiveChatIdState(fallback?.id ?? null);
    }
    await supabase
      .from("agent_chats")
      .update({ archived_at: ts, updated_at: ts })
      .eq("id", chatId)
      .eq("user_id", userId);
  }, [activeChatId, userId]);

  const renameChat = useCallback(
    async (chatId: string, title: string) => {
      const trimmed = title.trim();
      if (!trimmed) return;
      titleLockedIdsRef.current.add(chatId);
      const ts = nowIso();
      setChats((prev) =>
        prev.map((c) => (c.id === chatId ? { ...c, title: trimmed, updated_at: ts } : c)),
      );
      markDirty(chatId);
      const { error } = await supabase
        .from("agent_chats")
        .update({ title: trimmed, updated_at: ts })
        .eq("id", chatId)
        .eq("user_id", userId);
      if (error) setSaveError(error.message);
    },
    [markDirty, userId],
  );

  const refreshChatTitleFromServer = useCallback(
    async (chatId: string) => {
      if (titleLockedIdsRef.current.has(chatId)) return;
      if (titleGenerationInFlightRef.current.has(chatId)) return;
      const chat = chatsRef.current.find((c) => c.id === chatId);
      if (!chat?.turns.length) return;
      if (chat.title?.trim()) return;
      const firstUser = firstUserMessageText(chat.turns);
      if (!firstUser) return;
      titleGenerationInFlightRef.current.add(chatId);
      try {
        const title = await suggestAgentChatTitle(firstUser);
        if (!title || titleLockedIdsRef.current.has(chatId)) return;
        const latest = chatsRef.current.find((c) => c.id === chatId);
        if (latest?.title?.trim()) return;
        const ts = nowIso();
        setChats((prev) =>
          prev.map((c) => (c.id === chatId ? { ...c, title, updated_at: ts } : c)),
        );
        markDirty(chatId);
      } finally {
        titleGenerationInFlightRef.current.delete(chatId);
      }
    },
    [markDirty],
  );

  const deleteChat = useCallback(
    async (chatId: string) => {
      titleLockedIdsRef.current.delete(chatId);
      titleGenerationInFlightRef.current.delete(chatId);
      dirtyIdsRef.current.delete(chatId);
      const prev = chatsRef.current;
      const next = prev.filter((c) => c.id !== chatId);
      setChats(next);
      if (activeChatId === chatId) {
        if (next.length > 0) {
          setActiveChatId(next[0].id);
        } else {
          createChat({ activate: true });
        }
      }
      const { error } = await supabase.from("agent_chats").delete().eq("id", chatId).eq("user_id", userId);
      if (error) setSaveError(error.message);
    },
    [activeChatId, createChat, setActiveChatId, userId],
  );

  const loadMore = useCallback(async () => {
    if (!hasMore) return;
    let query = supabase
      .from("agent_chats")
      .select("*")
      .eq("user_id", userId)
      .is("archived_at", null)
      .order("updated_at", { ascending: false })
      .order("id", { ascending: false })
      .limit(PAGE_SIZE);
    if (cursor) {
      const clause = `updated_at.lt.${cursor.updated_at},and(updated_at.eq.${cursor.updated_at},id.lt.${cursor.id})`;
      query = query.or(clause);
    }
    const { data, error } = await query;
    if (error) {
      setLoadError(error.message);
      return;
    }
    const normalized = (data ?? []).map((r) => normalizeStoredAgentChat(r as Record<string, unknown>));
    setChats((prev) => {
      const map = new Map(prev.map((c) => [c.id, c]));
      for (const chat of normalized) map.set(chat.id, chat);
      return Array.from(map.values()).sort((a, b) => {
        if (a.updated_at === b.updated_at) return b.id.localeCompare(a.id);
        return b.updated_at.localeCompare(a.updated_at);
      });
    });
    const last = normalized.length > 0 ? normalized[normalized.length - 1] : undefined;
    setCursor(last ? { updated_at: last.updated_at, id: last.id } : cursor);
    setHasMore(normalized.length === PAGE_SIZE);
  }, [cursor, hasMore, userId]);

  const clearProactiveUnread = useCallback(() => {
    setProactiveUnread(false);
    try { localStorage.removeItem("recall_proactive_unread"); } catch { /* ignore */ }
  }, []);

  // Proactive SSE subscription.
  useEffect(() => {
    const cancel = connectProactiveStream(async (event) => {
      if (event.type === "connected") {
        const chatId = event.proactive_chat_id;
        proactiveChatIdRef.current = chatId;
        if (!chatsRef.current.find((c) => c.id === chatId)) {
          // Await the load so the inbox chat is present before the backlog drain
          // (which runs immediately after this event) appends turns to it.
          try {
            const { data } = await supabase
              .from("agent_chats")
              .select("*")
              .eq("id", chatId)
              .single();
            if (data) {
              const chat = normalizeStoredAgentChat(data as Record<string, unknown>);
              // Update the ref synchronously too — the proactive_job guard below
              // reads chatsRef.current, which the state-sync effect won't refresh
              // until after render.
              if (!chatsRef.current.find((c) => c.id === chat.id)) {
                chatsRef.current = [chat, ...chatsRef.current];
              }
              setChats((prev) => (prev.find((c) => c.id === chat.id) ? prev : [chat, ...prev]));
            }
          } catch (err) {
            console.warn("[proactive] failed to load proactive chat:", err);
          }
        }
        return;
      } else if (event.type === "proactive_job") {
        const chatId = proactiveChatIdRef.current;
        if (!chatId) {
          console.warn("[proactive] dropping job because proactive chat is not ready:", event.id);
          return { markSeen: false };
        }
        if (!chatsRef.current.find((c) => c.id === chatId)) {
          console.warn("[proactive] proactive chat not yet in memory, will retry:", event.id);
          return { markSeen: false };
        }
        const newTurn: AgentTurn = {
          id: event.id,
          role: "proactive",
          text: event.result.text,
          intentType: event.job_type,
          emailCards: event.result.email_cards as EmailCard[],
          calendarCards: event.result.calendar_cards as CalendarCard[],
          taskCards: event.result.task_cards as TaskCard[],
          timestamp: event.timestamp,
          audioB64: event.audio_b64 ?? undefined,
        };
        // Idempotent: never append the same proactive job twice (guards against
        // re-delivery on SSE reconnect). newTurn.id === event.id.
        replaceChatTurns(chatId, (prev) =>
          prev.some((t) => t.id === event.id) ? prev : [...prev, newTurn],
        );
        // Log every proactive delivery (all job types) to the notification center,
        // whether it ends up spoken or carded. Deduped by job id.
        addNotification({
          id: event.id,
          kind: event.job_type,
          message: proactiveLabel(event.job_type, event.result),
          ts: event.timestamp,
        });
        if (PROACTIVE_UNREAD_TYPES.has(event.job_type)) {
          setProactiveUnread(true);
          try { localStorage.setItem("recall_proactive_unread", "1"); } catch { /* ignore */ }
        }

        const ackAndMark = async (sessionKey?: string) => {
          const acked = await ackProactiveJob(event.id);
          if (acked && sessionKey) {
            try { sessionStorage.setItem(sessionKey, "1"); } catch { /* ignore */ }
          }
          return { markSeen: acked };
        };

        if (event.job_type !== "morning_brief") {
          return await ackAndMark();
        }

        const ssKey = `recall_brief_announced_${event.id}`;
        if (sessionStorage.getItem(ssKey)) {
          return await ackAndMark(ssKey);
        }

        if (!event.finished_at) {
          console.warn("[proactive] skipping morning brief announcement: missing finished_at", { jobId: event.id });
          return await ackAndMark(ssKey);
        }
        const finishedMs = Date.parse(event.finished_at);
        if (Number.isNaN(finishedMs)) {
          console.warn("[proactive] skipping morning brief announcement: invalid finished_at", {
            jobId: event.id,
            finished_at: event.finished_at,
          });
          return await ackAndMark(ssKey);
        }
        const ageMs = Date.now() - finishedMs;
        if (ageMs > MORNING_BRIEF_MAX_AGE_MS) {
          console.warn("[proactive] skipping morning brief announcement: stale brief", {
            jobId: event.id,
            age_ms: ageMs,
          });
          return await ackAndMark(ssKey);
        }

        // Mark this brief announced synchronously, BEFORE attempting playback, so a
        // reconnect or concurrent re-delivery can't re-announce it — the earlier
        // ssKey guard short-circuits to ackAndMark even while the first ack is in flight.
        try { sessionStorage.setItem(ssKey, "1"); } catch { /* ignore */ }

        // Quiet context (on a call / in a meeting): show a card instead of speaking,
        // and count it so the orb can nudge once the call ends. Placed before
        // fetchProactiveAnnounceAudio so we never synthesize audio we'd discard.
        if (await isQuietContext()) {
          void showQuietCard("morning_brief", event.result);
          bumpSuppressed();
          return await ackAndMark(ssKey);
        }

        // The Realtime row carries no audio — fetch the announcement on demand,
        // only now that the freshness guard has passed (avoids synthesizing for a
        // stale brief drained from a previous day's backlog).
        const audioB64 = await fetchProactiveAnnounceAudio();
        if (!audioB64) {
          console.warn("[proactive] morning brief announcement skipped: no audio", { jobId: event.id });
          return await ackAndMark(ssKey);
        }

        // Best-effort playback: attempt once, then ack regardless of whether audio
        // started. The brief is already displayed; audio is a bonus. Never leave the
        // job unacked — that caused re-delivery + double announcements on reconnect.
        if (await isMainForeground()) {
          const startStatus = await playAudio(audioB64).started;
          if (startStatus !== "started") {
            console.warn("[proactive] main brief playback did not start; acked anyway", { jobId: event.id, status: startStatus });
          }
          return await ackAndMark(ssKey);
        }

        const orbStatus = await requestOrbPlayback(event.id, audioB64);
        if (orbStatus !== "started") {
          console.warn("[proactive] orb brief playback did not start; acked anyway", { jobId: event.id, status: orbStatus });
        }
        return await ackAndMark(ssKey);
      }
      return;
    });
    return cancel;
  }, [isMainForeground, replaceChatTurns, requestOrbPlayback, userId]);

  // Initial load and legacy migration.
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      setLoading(true);
      setLoadError(null);
      setHasMore(true);
      setCursor(null);
      setChats([]);
      const { data, error } = await supabase
        .from("agent_chats")
        .select("*")
        .eq("user_id", userId)
        .is("archived_at", null)
        .order("updated_at", { ascending: false })
        .order("id", { ascending: false })
        .limit(PAGE_SIZE);
      if (cancelled) return;
      if (error) {
        setLoadError(error.message);
        setLoading(false);
        return;
      }
      let normalized = (data ?? []).map((r) => normalizeStoredAgentChat(r as Record<string, unknown>));
      if (normalized.length === 0) {
        const legacySession = localStorage.getItem(LEGACY_SESSION_KEY);
        if (legacySession) {
          const seeded = createChat({ sessionId: legacySession, activate: true });
          normalized = [seeded];
          localStorage.removeItem(LEGACY_SESSION_KEY);
        } else {
          normalized = [createChat({ activate: true })];
        }
      }
      setChats(normalized);
      const savedActive = localStorage.getItem(ACTIVE_CHAT_KEY);
      const chosen = normalized.find((c) => c.id === savedActive) ?? normalized[0];
      setActiveChatIdState(chosen?.id ?? null);
      setHasMore((data ?? []).length === PAGE_SIZE);
      const last = normalized.length > 0 ? normalized[normalized.length - 1] : undefined;
      setCursor(last ? { updated_at: last.updated_at, id: last.id } : null);
      setLoading(false);
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [createChat, userId]);

  // Primary close flush.
  useEffect(() => {
    const unlistenPromise = appWindow.onCloseRequested(async (event) => {
      event.preventDefault();
      await flushNow();
      await appWindow.destroy();
    });
    return () => {
      void unlistenPromise.then((fn) => fn());
    };
  }, [appWindow, flushNow]);

  // Backup flushes.
  useEffect(() => {
    const onBeforeUnload = () => { void flushNow(); };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") void flushNow();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("visibilitychange", onVisibility);
      // fire-and-forget final flush from cleanup
      void flushNow();
    };
  }, [flushNow]);

  const activeChat = useMemo(
    () => chats.find((c) => c.id === activeChatId) ?? null,
    [activeChatId, chats],
  );

  const value = useMemo<AgentChatsContextValue>(() => ({
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
    loadMore,
    hasMore,
    archiveChat,
    renameChat,
    deleteChat,
    replaceChatTurns,
    patchTurnInChat,
    flushNow,
    refreshChatTitleFromServer,
    proactiveUnread,
    clearProactiveUnread,
  }), [
    chats,
    activeChat,
    activeChatId,
    loading,
    loadError,
    saveError,
    sidebarPinned,
    setActiveChatId,
    createChat,
    loadMore,
    hasMore,
    archiveChat,
    renameChat,
    deleteChat,
    replaceChatTurns,
    patchTurnInChat,
    flushNow,
    refreshChatTitleFromServer,
    proactiveUnread,
    clearProactiveUnread,
  ]);

  return <AgentChatsContext.Provider value={value}>{children}</AgentChatsContext.Provider>;
}

export function useAgentChats() {
  const ctx = useContext(AgentChatsContext);
  if (!ctx) throw new Error("useAgentChats must be used within AgentChatsProvider");
  return ctx;
}

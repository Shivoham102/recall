import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AgentChat } from "../../types/agentTurn";
import { chatRenameInitialDraft, sidebarDisplayTitle } from "../../utils/agentChatDisplay";

interface Props {
  chats: AgentChat[];
  activeChatId: string | null;
  visible: boolean;
  pinned: boolean;
  loadError: string | null;
  saveError: string | null;
  hasMore: boolean;
  proactiveUnread: boolean;
  onTogglePinned: () => void;
  onNewChat: () => void;
  onSelectChat: (chatId: string) => void;
  onRenameChat: (chatId: string, title: string) => void | Promise<void>;
  onDeleteChat: (chatId: string) => void | Promise<void>;
  onLoadMore: () => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const deltaMin = Math.floor((Date.now() - t) / 60000);
  if (deltaMin < 1) return "now";
  if (deltaMin < 60) return `${deltaMin}m`;
  const h = Math.floor(deltaMin / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

function IconPencil() {
  return (
    <svg className="agent-chat-menu__icon" width="16" height="16" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"
      />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg className="agent-chat-menu__icon" width="16" height="16" viewBox="0 0 24 24" aria-hidden>
      <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M3 6h18" />
      <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" d="M10 11v6M14 11v6" />
    </svg>
  );
}

export function AgentChatSidebar({
  chats,
  activeChatId,
  visible,
  pinned,
  loadError,
  saveError,
  hasMore,
  proactiveUnread,
  onTogglePinned,
  onNewChat,
  onSelectChat,
  onRenameChat,
  onDeleteChat,
  onLoadMore,
  onPointerEnter,
  onPointerLeave,
}: Props) {
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const menuAnchorRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!menuOpenId) return;
    const onDocPointerDown = (e: PointerEvent) => {
      const el = menuAnchorRef.current;
      if (el && !el.contains(e.target as Node)) setMenuOpenId(null);
    };
    document.addEventListener("pointerdown", onDocPointerDown, true);
    return () => document.removeEventListener("pointerdown", onDocPointerDown, true);
  }, [menuOpenId]);

  const beginRename = useCallback((chat: AgentChat) => {
    setMenuOpenId(null);
    setRenamingId(chat.id);
    setRenameDraft(chatRenameInitialDraft(chat));
  }, []);

  const submitRename = useCallback(async () => {
    if (!renamingId) return;
    const t = renameDraft.trim();
    if (t) await Promise.resolve(onRenameChat(renamingId, t));
    setRenamingId(null);
  }, [onRenameChat, renameDraft, renamingId]);

  const cancelRename = useCallback(() => {
    setRenamingId(null);
  }, []);

  const requestDelete = useCallback(
    (chatId: string) => {
      setMenuOpenId(null);
      void onDeleteChat(chatId);
    },
    [onDeleteChat],
  );

  useEffect(() => {
    if (!renamingId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelRename();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [renamingId, cancelRename]);

  return (
    <aside
      className={`agent-sidebar ${visible ? "agent-sidebar--open" : ""}`}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      <div className="agent-sidebar__header">
        <button type="button" className="agent-sidebar__pin" onClick={onTogglePinned} title={pinned ? "Unpin" : "Pin open"}>
          {pinned ? "⇤" : "⇥"}
        </button>
        <button type="button" className="agent-sidebar__new" onClick={onNewChat}>+ New chat</button>
      </div>

      {loadError && (
        <div className="agent-sidebar__error">
          <span>{loadError}</span>
        </div>
      )}
      {saveError && (
        <div className="agent-sidebar__save-error">
          <span>Could not save changes</span>
        </div>
      )}

      <div className="agent-sidebar__list">
        {chats.map((chat) => (
          <div
            key={chat.id}
            className={[
              "agent-chat-row",
              chat.id === activeChatId ? "agent-chat-row--active" : "",
              menuOpenId === chat.id ? "agent-chat-row--menu-open" : "",
              chat.is_proactive_inbox ? "agent-chat-row--proactive" : "",
            ].filter(Boolean).join(" ")}
          >
            {renamingId === chat.id ? (
              <form
                className="agent-chat-row__rename"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submitRename();
                }}
              >
                <input
                  className="agent-chat-row__rename-input"
                  value={renameDraft}
                  onChange={(e) => setRenameDraft(e.target.value)}
                  placeholder="Chat title"
                  autoFocus
                />
                <div className="agent-chat-row__rename-actions">
                  <button type="submit" className="agent-chat-row__rename-save">Save</button>
                  <button type="button" className="agent-chat-row__rename-cancel" onClick={cancelRename}>Cancel</button>
                </div>
              </form>
            ) : (
              <>
                <button
                  type="button"
                  className="agent-chat-row__main"
                  onClick={() => onSelectChat(chat.id)}
                >
                  <div className="agent-chat-row__title">
                    {chat.is_proactive_inbox ? "◈ " : ""}
                    {sidebarDisplayTitle(chat)}
                    {chat.is_proactive_inbox && proactiveUnread && (
                      <span className="agent-chat-row__unread" aria-label="New proactive updates" />
                    )}
                  </div>
                  <div className="agent-chat-row__meta">
                    <span>{relativeTime(chat.updated_at)}</span>
                  </div>
                </button>
                <div
                  className="agent-chat-row__menu-anchor"
                  ref={menuOpenId === chat.id ? menuAnchorRef : undefined}
                >
                  {!chat.is_proactive_inbox && (
                  <button
                    type="button"
                    className="agent-chat-row__kebab"
                    aria-label="Chat options"
                    aria-expanded={menuOpenId === chat.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpenId((id) => (id === chat.id ? null : chat.id));
                    }}
                  >
                    ⋯
                  </button>
                  )}
                  {menuOpenId === chat.id && (
                    <div className="agent-chat-menu" role="menu">
                      {!chat.is_proactive_inbox && (
                        <button
                          type="button"
                          className="agent-chat-menu__item"
                          role="menuitem"
                          onClick={() => beginRename(chat)}
                        >
                          <IconPencil />
                          <span>Rename</span>
                        </button>
                      )}
                      {!chat.is_proactive_inbox && (
                        <button
                          type="button"
                          className="agent-chat-menu__item agent-chat-menu__item--danger"
                          role="menuitem"
                          onClick={() => void requestDelete(chat.id)}
                        >
                          <IconTrash />
                          <span>Delete</span>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        ))}
      </div>

      {hasMore && (
        <button type="button" className="agent-sidebar__more" onClick={onLoadMore}>
          Load older
        </button>
      )}
    </aside>
  );
}

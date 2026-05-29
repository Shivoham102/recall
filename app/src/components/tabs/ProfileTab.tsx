import { useCallback, useEffect, useState } from "react";
import { AuthUser } from "../../hooks/useAuth";
import { useTheme } from "../../hooks/useTheme";
import { LearnedProfile, getItems, getLearned } from "../../services/api";
import { formatRecurrence } from "../../utils/dateFormat";

interface Props {
  user: AuthUser;
  onLogout: () => void;
  onShowOnboarding?: () => void;
}

export function ProfileTab({ user, onLogout, onShowOnboarding }: Props) {
  const [itemCount, setItemCount] = useState<number | null>(null);
  const [learned, setLearned] = useState<LearnedProfile | null>(null);
  const [reconnectMsg, setReconnectMsg] = useState("");
  const { theme, setTheme } = useTheme();

  const loadProfileStats = useCallback(() => {
    getItems({ status: "open", limit: 500 })
      .then((items) => setItemCount(items.length))
      .catch(() => {});
    getLearned()
      .then(setLearned)
      .catch(() => setLearned({ auto_brief: [], habits: [], suggestions: { accepted: 0, dismissed: 0, pending: 0, total: 0 } }));
  }, []);

  useEffect(() => {
    loadProfileStats();
  }, [loadProfileStats]);

  useEffect(() => {
    const onFocus = () => loadProfileStats();
    const onVisibility = () => {
      if (document.visibilityState === "visible") loadProfileStats();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [loadProfileStats]);

  const initials = user.name
    ? user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : (user.email[0] ?? "?").toUpperCase();

  return (
    <div className="profile-tab">
      {/* Avatar + identity */}
      <div className="profile-hero">
        <div className="profile-avatar">{initials}</div>
        <div className="profile-identity">
          {user.name && <div className="profile-name">{user.name}</div>}
          <div className="profile-email">{user.email}</div>
        </div>
      </div>

      {/* Stats */}
      <div className="profile-section">
        <div className="profile-section__label">Memory</div>
        <div className="profile-stat-row">
          <span className="profile-stat__key">Open items</span>
          <span className="profile-stat__val">
            {itemCount === null ? "-" : itemCount}
          </span>
        </div>
      </div>

      {/* Appearance */}
      <div className="profile-section">
        <div className="profile-section__label">Appearance</div>
        <div className="profile-segment" role="group" aria-label="Theme">
          <button
            className={`profile-segment__btn ${theme === "light" ? "profile-segment__btn--active" : ""}`}
            onClick={() => setTheme("light")}
            aria-pressed={theme === "light"}
          >
            Light
          </button>
          <button
            className={`profile-segment__btn ${theme === "dark" ? "profile-segment__btn--active" : ""}`}
            onClick={() => setTheme("dark")}
            aria-pressed={theme === "dark"}
          >
            Dark
          </button>
        </div>
      </div>

      {/* Connections */}
      <div className="profile-section">
        <div className="profile-section__label">Connected</div>
        <div className="profile-connection">
          <div className="profile-connection__icon">G</div>
          <div className="profile-connection__info">
            <div className="profile-connection__name">Google</div>
            <div className="profile-connection__desc">Gmail · Calendar · Identity</div>
          </div>
          <div className="profile-connection__badge">Active</div>
        </div>
        <button
          className="profile-btn"
          onClick={() => {
            setReconnectMsg("Sign in again to refresh Google permissions.");
            onLogout();
          }}
        >
          Reconnect Google
        </button>
        {reconnectMsg && (
          <div className="profile-stat-row">
            <span className="profile-stat__key profile-stat__key--muted">{reconnectMsg}</span>
          </div>
        )}
      </div>

      {/* What I've learned */}
      {learned !== null && (() => {
        const isEmpty =
          learned.auto_brief.length === 0 &&
          learned.habits.length === 0 &&
          learned.suggestions.total === 0;
        return (
          <div className="profile-section profile-learned">
            <div className="profile-section__label">What I've learned</div>

            {isEmpty && (
              <div className="profile-stat-row">
                <span className="profile-stat__key profile-stat__key--muted">
                  Keep using Recall. It starts learning your habits after a few sessions.
                </span>
              </div>
            )}

            {/* Habits learned (recurring reminders) */}
            {learned.habits.length > 0 && (
              <div className="learned-group">
                <div className="learned-group__title">Habits I keep for you</div>
                {learned.habits.map((h) => (
                  <div key={h.id} className="learned-habit">
                    <span className="learned-habit__icon">↻</span>
                    <span className="learned-habit__name">{h.content}</span>
                    {h.recurrence && (
                      <span className="learned-habit__cadence">{formatRecurrence(h.recurrence)}</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* On auto-brief (no counts) */}
            {learned.auto_brief.length > 0 && (
              <div className="learned-group">
                <div className="learned-group__title">Now on your auto-brief</div>
                <div className="learned-chips">
                  {learned.auto_brief.map((label) => (
                    <span key={label} className="learned-chip">{label}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Suggestion outcome */}
            {learned.suggestions.total > 0 && (
              <div className="learned-group">
                <div className="learned-group__title">Routines I suggested</div>
                <div className="learned-suggest">
                  <span className="learned-suggest__big">{learned.suggestions.accepted}</span>
                  <span className="learned-suggest__small">
                    of {learned.suggestions.total} accepted
                  </span>
                  {learned.suggestions.pending > 0 && (
                    <span className="learned-suggest__pending">
                      {learned.suggestions.pending} waiting for you
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })()}

      {/* Help */}
      {onShowOnboarding && (
        <div className="profile-section">
          <div className="profile-section__label">Help</div>
          <button className="profile-btn" onClick={onShowOnboarding}>
            Show welcome tour
          </button>
        </div>
      )}

      {/* Sign out */}
      <div className="profile-section profile-section--footer">
        <button className="profile-signout" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </div>
  );
}

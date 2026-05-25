import { useEffect, useState } from "react";
import { AuthUser } from "../../hooks/useAuth";
import { BehaviorPattern, getItems, getPatterns } from "../../services/api";

interface Props {
  user: AuthUser;
  onLogout: () => void;
}

function PatternBar({ frequency }: { frequency: number }) {
  const filled = Math.min(frequency, 5);
  return (
    <span className="pattern-bar" aria-label={`${frequency} occurrences`}>
      {"█".repeat(filled)}{"░".repeat(Math.max(0, 5 - filled))}
    </span>
  );
}

export function ProfileTab({ user, onLogout }: Props) {
  const [itemCount, setItemCount] = useState<number | null>(null);
  const [patterns, setPatterns] = useState<BehaviorPattern[] | null>(null);
  const [reconnectMsg, setReconnectMsg] = useState("");

  useEffect(() => {
    getItems({ status: "open", limit: 500 })
      .then((items) => setItemCount(items.length))
      .catch(() => {});
    getPatterns()
      .then((p) => setPatterns(p.filter((x) => x.frequency >= 2)))
      .catch(() => setPatterns([]));
  }, []);

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
          className="profile-signout"
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
      {patterns !== null && (
        <div className="profile-section">
          <div className="profile-section__label">What I've learned</div>
          {patterns.length === 0 ? (
            <div className="profile-stat-row">
              <span className="profile-stat__key profile-stat__key--muted">
                Keep using Recall. Patterns appear after a few sessions.
              </span>
            </div>
          ) : (
            patterns.map((p) => (
              <div key={p.id} className="profile-pattern-row">
                <span className="profile-pattern__label">{p.query_template}</span>
                <span className="profile-pattern__bar">
                  <PatternBar frequency={p.frequency} />
                </span>
                <span className="profile-pattern__count">{p.frequency}x</span>
                {p.auto_run && (
                  <span className="profile-pattern__auto-tag">auto-brief</span>
                )}
              </div>
            ))
          )}
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

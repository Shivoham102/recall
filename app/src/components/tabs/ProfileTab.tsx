import { useEffect, useState } from "react";
import { AuthUser } from "../../hooks/useAuth";
import { getItems } from "../../services/api";

interface Props {
  user: AuthUser;
  onLogout: () => void;
}

export function ProfileTab({ user, onLogout }: Props) {
  const [itemCount, setItemCount] = useState<number | null>(null);

  useEffect(() => {
    getItems({ status: "open", limit: 500 })
      .then((items) => setItemCount(items.length))
      .catch(() => {});
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
            {itemCount === null ? "—" : itemCount}
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
      </div>

      {/* Sign out */}
      <div className="profile-section profile-section--footer">
        <button className="profile-signout" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { setAuthToken } from "../hooks/useAuth";
import { getBase } from "../services/backend";
const POLL_TIMEOUT_MS = 3 * 60 * 1000; // auto-cancel after 3 minutes

interface Props {
  onLogin: () => void;
}

export function LoginScreen({ onLogin }: Props) {
  const [status, setStatus] = useState<"idle" | "waiting" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const appWindow = useRef(getCurrentWindow()).current;

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
  };

  useEffect(() => () => stopPolling(), []);

  const handleCancel = () => {
    stopPolling();
    setStatus("idle");
  };

  const handleSignIn = async () => {
    try {
      setStatus("waiting");
      const res = await fetch(`${await getBase()}/auth/url`);
      if (!res.ok) throw new Error("Backend unavailable — is the server running?");
      const { url, state } = await res.json();

      await openUrl(url);

      // Poll every 2 s for JWT
      pollRef.current = setInterval(async () => {
        try {
          const pollRes = await fetch(`${await getBase()}/auth/poll?state=${state}`);
          const data = await pollRes.json();
          if (data.ready && data.token) {
            stopPolling();
            setAuthToken(data.token);
            onLogin();
          }
        } catch {
          // ignore transient network errors while polling
        }
      }, 2000);

      // Auto-cancel if the browser tab was closed without completing sign-in
      timeoutRef.current = setTimeout(() => {
        stopPolling();
        setStatus("error");
        setErrorMsg("Sign-in timed out — the browser tab may have been closed. Try again.");
      }, POLL_TIMEOUT_MS);
    } catch (e) {
      stopPolling();
      setStatus("error");
      setErrorMsg(String(e));
    }
  };

  return (
    <div className="login-screen">
      <div className="titlebar" data-tauri-drag-region>
        <span className="titlebar__logo" style={{ cursor: "default" }}>
          <span className="titlebar__dot" />
          Recall
        </span>
        <div style={{ flex: 1 }} />
        <div className="titlebar__controls">
          <button className="wm-btn" onClick={() => appWindow.minimize()} title="Minimize">─</button>
          <button className="wm-btn" onClick={() => appWindow.toggleMaximize()} title="Maximize">⬜</button>
          <button className="wm-btn wm-btn--close" onClick={() => appWindow.hide()} title="Close">✕</button>
        </div>
      </div>
      <div className="login-body">
      <div className="login-card">
        <div className="login-logo">
          <span className="login-logo__dot" />
          Recall
        </div>
        <p className="login-tagline">Your voice-powered working memory</p>

        {status === "idle" && (
          <button className="login-btn" onClick={handleSignIn}>
            Sign in with Google
          </button>
        )}

        {status === "waiting" && (
          <div className="login-waiting">
            <div className="login-spinner" />
            <p>Complete sign-in in your browser…</p>
            <button className="login-btn login-btn--ghost" onClick={handleCancel}>
              Cancel
            </button>
          </div>
        )}

        {status === "error" && (
          <>
            <p className="login-error">{errorMsg}</p>
            <button className="login-btn" onClick={() => setStatus("idle")}>
              Try again
            </button>
          </>
        )}
      </div>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { onOpenUrl } from "@tauri-apps/plugin-deep-link";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { supabase } from "../services/supabase";

const GMAIL_SCOPES = [
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/gmail.send",
  "https://www.googleapis.com/auth/gmail.compose",
  "https://www.googleapis.com/auth/calendar",
  "https://www.googleapis.com/auth/calendar.events",
].join(" ");

interface Props {
  onLogin: () => void;
}

export function LoginScreen({ onLogin }: Props) {
  const [status, setStatus] = useState<"idle" | "waiting" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const unlistenRef = useRef<(() => void) | null>(null);
  const appWindow = useRef(getCurrentWindow()).current;

  const stopListening = () => {
    unlistenRef.current?.();
    unlistenRef.current = null;
  };

  useEffect(() => () => stopListening(), []);

  const handleCancel = () => {
    stopListening();
    setStatus("idle");
  };

  const handleSignIn = async () => {
    try {
      setStatus("waiting");

      // Register deep link listener BEFORE opening browser
      unlistenRef.current = await onOpenUrl(async (urls) => {
        stopListening();
        const url = urls[0] ?? "";
        const fragment = url.includes("#") ? url.split("#")[1] : url.split("?")[1] ?? "";
        const params = new URLSearchParams(fragment);
        const accessToken = params.get("access_token");
        const refreshToken = params.get("refresh_token");
        const providerToken = params.get("provider_token");
        const providerRefreshToken = params.get("provider_refresh_token");

        if (!accessToken || !refreshToken) {
          setStatus("error");
          setErrorMsg("OAuth callback missing tokens — try again.");
          return;
        }

        const { data, error } = await supabase.auth.setSession({
          access_token: accessToken,
          refresh_token: refreshToken,
        });

        if (error || !data.session) {
          setStatus("error");
          setErrorMsg(error?.message ?? "Failed to set session.");
          return;
        }

        // Persist Google refresh token so voice agent tools can call Gmail/Calendar
        if (providerRefreshToken && data.session.user) {
          await supabase.from("users").upsert({
            id: data.session.user.id,
            email: data.session.user.email,
            google_refresh_token: providerRefreshToken,
            google_access_token: providerToken ?? null,
            google_token_expiry: providerToken
              ? new Date(Date.now() + 3600 * 1000).toISOString()
              : null,
          }, { onConflict: "id" });
        }

        appWindow.show();
        appWindow.setFocus();
        onLogin();
      });

      const apiBase = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE ?? "http://localhost:8000";
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${apiBase}/auth/callback`,
          scopes: GMAIL_SCOPES,
          queryParams: { access_type: "offline", prompt: "consent" },
          skipBrowserRedirect: true,
        },
      });

      if (error || !data.url) {
        throw new Error(error?.message ?? "Failed to get OAuth URL");
      }

      await openUrl(data.url);
    } catch (e) {
      stopListening();
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

import { useEffect, useRef, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { onOpenUrl } from "@tauri-apps/plugin-deep-link";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { supabase } from "../services/supabase";
import { storeGoogleTokens } from "../services/api";
import { Orb } from "./Orb/OrbCanvas";

const GMAIL_SCOPES = [
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/gmail.compose",
  "https://www.googleapis.com/auth/calendar.events",
].join(" ");

async function retryGoogleTokenSync(input: {
  provider_token: string | null;
  provider_refresh_token: string;
  google_token_expiry: string | null;
}) {
  let lastError: unknown = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await storeGoogleTokens(input);
      return;
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, 300 * (attempt + 1)));
    }
  }
  throw lastError;
}

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
          setErrorMsg("OAuth callback missing tokens. Try again.");
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

        if (!providerRefreshToken) {
          await supabase.auth.signOut();
          setStatus("error");
          setErrorMsg("Google did not return offline access. Try signing in again.");
          return;
        }

        // provider_refresh_token is only available in this callback. If syncing it
        // fails, force re-auth instead of showing a falsely connected account.
        try {
          await retryGoogleTokenSync({
            provider_refresh_token: providerRefreshToken,
            provider_token: providerToken,
            google_token_expiry: providerToken
              ? new Date(Date.now() + 3600 * 1000).toISOString()
              : null,
          });
        } catch (err) {
          await supabase.auth.signOut();
          setStatus("error");
          setErrorMsg(`Google connection failed. Please sign in again. ${String(err)}`);
          return;
        }

        appWindow.show();
        appWindow.setFocus();
        onLogin();
      });

      const apiBase = import.meta.env.VITE_API_BASE || "https://recall-seven-livid.vercel.app";
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
          <div className="login-orb-wrap">
            <Orb state={status === "waiting" ? "processing" : status === "error" ? "error" : "idle"} size={96} />
          </div>
          <h1 className="login-wordmark">Recall</h1>
          <p className="login-tagline">Your voice-powered working memory</p>

          {status === "idle" && (
            <button className="login-btn" onClick={handleSignIn}>
              Sign in with Google
            </button>
          )}

          {status === "waiting" && (
            <div className="login-waiting">
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

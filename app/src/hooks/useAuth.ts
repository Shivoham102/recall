import { useState, useEffect, useCallback } from "react";
import { supabase } from "../services/supabase";

export interface AuthUser {
  user_id: string;
  email: string;
  name: string;
}

export async function getAuthHeader(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    return { Authorization: `Bearer ${session.access_token}` };
  }
  return {};
}

export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const syncUser = useCallback(async () => {
    setIsLoading(true);
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) supabase.realtime.setAuth(session.access_token);
    if (session?.user) {
      setUser({
        user_id: session.user.id,
        email: session.user.email ?? "",
        name: session.user.user_metadata?.full_name ?? session.user.email ?? "",
      });
    } else {
      setUser(null);
    }
    setIsLoading(false);
  }, []);

  useEffect(() => {
    syncUser();
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        // Realtime embeds the JWT in the websocket handshake; push the refreshed
        // token so the proactive_jobs subscription keeps authorizing after rotation.
        if (session?.access_token) supabase.realtime.setAuth(session.access_token);
        if (session?.user) {
          setUser({
            user_id: session.user.id,
            email: session.user.email ?? "",
            name: session.user.user_metadata?.full_name ?? session.user.email ?? "",
          });
        } else {
          setUser(null);
        }
        setIsLoading(false);
      },
    );
    return () => subscription.unsubscribe();
  }, [syncUser]);

  const logout = useCallback(async () => {
    await supabase.auth.signOut();
    setUser(null);
  }, []);

  return { user, isLoading, logout, refreshAuth: syncUser };
}

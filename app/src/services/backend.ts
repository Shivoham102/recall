import { invoke } from "@tauri-apps/api/core";

let _basePromise: Promise<string> | null = null;

export function resetBackendBase() {
  _basePromise = null;
}

export function getBase(): Promise<string> {
  if (!_basePromise) {
    _basePromise = (async () => {
      // Cloud deployment — use Vercel base URL if set
      const vercelUrl = (import.meta as { env?: Record<string, string> }).env
        ?.VITE_API_BASE;
      if (vercelUrl) return vercelUrl;

      // Web-only dev override
      const devUrl = (import.meta as { env?: Record<string, string> }).env
        ?.VITE_BACKEND_URL;
      if (devUrl) return devUrl;

      // Local Tauri sidecar — poll until backend reports its port
      for (let i = 0; i < 50; i++) {
        const port = await invoke<number | null>("get_backend_port").catch(
          () => null,
        );
        if (port) return `http://127.0.0.1:${port}`;
        await new Promise((r) => setTimeout(r, 200));
      }
      _basePromise = null;
      throw new Error("Backend did not start within 10s");
    })();
  }
  return _basePromise;
}

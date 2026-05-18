# Recall — Claude Notes

## Releasing

**Always use the release script. Never tag manually.**

```powershell
./scripts/bump-version.ps1 0.7.0
```

This updates `tauri.conf.json`, `app/package.json`, and `Cargo.toml` to match,
then commits, tags, and pushes. Skipping it causes installer filenames to show
the old version (e.g. `Recall_0.5.0_x64-setup.exe` on a v0.6.0 release).

## Project layout

- `app/` — Tauri desktop app (Vite + React + Rust)
- `backend/` — FastAPI Python backend (deployed to Vercel)
- `web/` — Next.js marketing site (deployed to Vercel, same project)
- `scripts/` — dev utilities

## Vercel

Single Vercel project at repo root. `vercel.json` routes `/health`, `/capture/*`,
`/jobs/*`, `/items*`, `/reminders/*`, `/agent/*`, `/auth/*`, `/debug/*` to
`backend/main.py`; everything else falls through to the Next.js build in `web/`.

## GitHub Actions secrets required for release builds

`VITE_API_BASE`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` — all set.

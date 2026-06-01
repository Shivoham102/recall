# Recall — Claude Notes

## User-facing text

Never use em dashes (—) in any text a user will see: UI/display strings, onboarding
copy, and agent-generated or hardcoded output (spoken responses, brief/triage/suggestion
text, and system-prompt examples the model will mimic). Use commas, periods, colons,
or semicolons instead. Code comments and developer logs are exempt — this rule is about
product text in `app/` and agent strings in `backend/`.

## Commit messages

Keep them concise. Say **what** changed — features added/patched/removed, behavior
that changed — not **how** it's implemented. Conventional Commits subject
(`feat:`, `fix:`, `chore:` …). Add a short bullet body only when there's more than
one notable change. Some technical detail is fine, but no line-by-line internals,
ref/variable names, or implementation play-by-play.

Good: `fix(proactive): stop duplicate morning brief showing twice`
Too technical: `fix: gate ackAndMark on requestGenRef so handleStop skips reset`

## Releasing

**Always use the release script. Never tag manually.**

```powershell
./scripts/bump-version.ps1 0.7.0
```

This updates `tauri.conf.json`, `app/package.json`, and `Cargo.toml` to match,
then commits, tags, and pushes. Skipping it causes installer filenames to show
the old version (e.g. `Recall_0.5.0_x64-setup.exe` on a v0.6.0 release).

If a release ends up as a Draft or has stale assets, delete it on GitHub first,
then delete and re-push the tag to re-trigger GitHub Actions:

```bash
git tag -d v0.6.0
git push origin :refs/tags/v0.6.0
git tag v0.6.0
git push origin v0.6.0
```

### Version numbering — SemVer (semver.org)

`MAJOR.MINOR.PATCH`

| Bump | When |
|------|------|
| PATCH (0.6.**1**) | Bug fixes, no new features |
| MINOR (0.**7**.0) | New features, backwards-compatible |
| MAJOR (**1**.0.0) | Breaking changes or major overhaul |

Examples for this project:
- Fix HTML entity decode bug → PATCH
- Add new proactive job or UI feature → MINOR
- Full rewrite / API breaking change → MAJOR

Pre-1.0: MINOR for features, PATCH for fixes. MAJOR stays 0 until stable public release.

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

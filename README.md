# Recall

A conversational voice assistant for managing working memory throughout the day. Not a logger you talk at — an agent you talk with.

Hit a hotkey, speak a thought, and Recall captures it, classifies it, and responds. Ask it what's open, what you got done, or what you said you'd follow up on. Draft emails, check your calendar, create files — all by voice, all in context.

---

## What it does

- **Quick capture** — global hotkey (`Ctrl+Shift+Space`), one sentence, done in ~10 seconds
- **Agentic conversations** — multi-turn voice chat with real tool use: search your memory, check Gmail, read your calendar, create files
- **Intent classification** — automatically tags each input as a task, blocker, follow-up, progress update, or note
- **RAG-powered memory** — before responding, the agent retrieves semantically similar items from your history and reasons over their status and timestamps
- **Reminders** — set due dates by voice ("remind me at 3 PM"); the app delivers an audio reminder at the right time, even if it was closed in between
- **Gmail integration** — read inbox updates, draft emails in your writing style (fetches your sent history first)
- **Google Calendar integration** — list upcoming events, create events by voice with confirmation
- **Session persistence** — conversation history survives backend restarts
- **Google SSO** — sign in once with Google; the same consent grants Gmail and Calendar access

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Tauri v2 + React (two windows)                              │
│                                                              │
│  Orb window  — Ctrl+Shift+Space, quick voice capture         │
│  MainApp     — 5 tabs: Agent · Tasks · Transcripts ·         │
│                        Reminders · Profile                   │
│  System tray icon for minimize/restore                       │
└──────────────────┬───────────────────────────────────────────┘
                   │ HTTP + Server-Sent Events (SSE)
                   │ Authorization: Bearer <JWT> on every request
┌──────────────────▼───────────────────────────────────────────┐
│  FastAPI backend (localhost:8000)                             │
│                                                              │
│  GET  /auth/url             → Google OAuth URL + state       │
│  GET  /auth/callback        → exchange code, issue JWT       │
│  GET  /auth/poll?state=     → frontend polls for JWT         │
│  GET  /auth/me              → validate JWT, return user info │
│                                                              │
│  POST /capture/stream   audio or text → SSE event stream     │
│  POST /capture          audio → single JSON response (legacy)│
│  GET  /items            list stored recall items             │
│  PATCH /items/:id       update item status / due date        │
│  GET  /reminders/pending  undelivered future reminders       │
│  GET  /reminders/due      due now → delivers TTS audio       │
│  POST /reminders/dismiss  mark missed reminders as seen      │
│                                                              │
│  Agent loop (Claude claude-sonnet-4-6 + tool use):          │
│    classify_intent · recall_search · recall_update_item      │
│    surface_tasks · file_create                               │
│    gmail_get_updates · surface_cards · gmail_find_contact    │
│    gmail_fetch_style_samples · gmail_draft                   │
│    calendar_list · calendar_create                           │
│                                                              │
│  faster-whisper  STT (local, CPU)                            │
│  OpenAI          embeddings (text-embedding-3-small)         │
│                  TTS (tts-1, nova voice)                     │
└──────────────────┬───────────────────────────────────────────┘
                   │ pgvector + JSONB
┌──────────────────▼───────────────────────────────────────────┐
│  Supabase (PostgreSQL + pgvector)                            │
│  users        — Google identity + OAuth tokens               │
│  recall_items — items, embeddings, due dates, reminders      │
│  sessions     — persisted conversation history per session   │
└──────────────────────────────────────────────────────────────┘
```

**Streaming capture flow:** hold mic → webm/opus recorded in browser → `POST /capture/stream` → faster-whisper transcribes → RAG retrieves similar items → Claude agentic loop with tools → SSE yields transcript, tool steps, spoken text, and TTS audio in real time → item stored if actionable.

**Auth flow:** app opens → checks localStorage for JWT → if missing, shows login screen → user clicks "Sign in with Google" → backend builds OAuth URL (openid + Gmail + Calendar scopes) → system browser opens → user consents → backend exchanges code for tokens, stores them in Supabase, issues a 30-day JWT → frontend polls `/auth/poll`, receives JWT → stores in localStorage → renders main app. JWT is auto-generated and persisted to `AppData/Local/Recall/` for shipped builds.

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri v2 |
| Frontend | React 19 + TypeScript + Vite |
| Backend | FastAPI + Python 3.11+ |
| Auth | Google OAuth 2.0 SSO + JWT (HS256, PyJWT) |
| STT | faster-whisper (local, `base` model, CPU) |
| TTS | OpenAI TTS API (`tts-1`, `nova` voice) |
| Agent | Anthropic Claude `claude-sonnet-4-6` with prompt caching |
| Embeddings | OpenAI `text-embedding-3-small` (1536 dims) |
| Database | Supabase — PostgreSQL + pgvector (HNSW index) |
| Google APIs | Gmail + Google Calendar via SSO OAuth 2.0 |

---

## Project structure

```
recall/
├── .env.example
├── db/
│   └── schema.sql            ← run in Supabase SQL editor
├── test_backend.py           ← end-to-end test script
├── backend/
│   ├── main.py               ← FastAPI app + route registration
│   ├── agent.py              ← Claude sessions, agentic loop, prompt caching
│   ├── auth.py               ← JWT encode/decode, get_current_user dependency
│   ├── context.py            ← ContextVar for per-request user_id propagation
│   ├── session_store.py      ← Supabase-backed session persistence
│   ├── rag.py                ← embed(), retrieve_similar(), store_item()
│   ├── stt.py                ← faster-whisper transcription
│   ├── tts.py                ← OpenAI TTS synthesis
│   ├── db.py                 ← Supabase client
│   ├── google_auth.py        ← get_credentials_for_user() reads tokens from Supabase
│   ├── routes/
│   │   ├── auth.py           ← GET /auth/url|callback|poll|me (OAuth + JWT)
│   │   ├── agent_stream.py   ← POST /capture/stream (SSE, main path)
│   │   ├── capture.py        ← POST /capture (legacy single-response)
│   │   ├── query.py          ← POST /query (text-only)
│   │   ├── items.py          ← GET /items, PATCH /items/:id
│   │   └── reminders.py      ← GET /reminders/*, POST /reminders/dismiss
│   ├── tools/
│   │   ├── __init__.py       ← TOOL_DEFINITIONS + TOOL_REGISTRY
│   │   ├── memory.py         ← recall_search, recall_update_item, surface_tasks
│   │   ├── google_services.py← Gmail + Calendar tools
│   │   └── filesystem.py     ← file_create
│   └── requirements.txt
└── app/
    └── src/
        ├── components/
        │   ├── MainApp.tsx         ← tab router + reminder scheduler
        │   ├── LoginScreen.tsx     ← Google SSO login UI with polling
        │   ├── OrbWindow.tsx       ← hotkey-triggered orb (Ctrl+Shift+Space)
        │   ├── FloatingWindow.tsx  ← streaming voice capture window
        │   ├── VoiceButton.tsx
        │   ├── ChatHistory.tsx
        │   └── tabs/
        │       ├── AgentTab.tsx        ← streaming chat, tool steps, email/task cards
        │       ├── TasksTab.tsx
        │       ├── TranscriptsTab.tsx
        │       ├── RemindersTab.tsx
        │       └── ProfileTab.tsx      ← user info, Google connection, sign out
        ├── hooks/
        │   ├── useRecorder.ts
        │   └── useAuth.ts          ← JWT storage, /auth/me validation, logout
        └── services/
            ├── api.ts                  ← captureStream(), items, reminders API (auth headers)
            └── reminderScheduler.ts    ← timer management, missed-reminder detection
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python --version` |
| Node.js 18+ | `node --version` |
| pnpm | `npm install -g pnpm` |
| Rust + Cargo | [rustup.rs](https://rustup.rs) — required for Tauri |
| ffmpeg | `winget install Gyan.FFmpeg` — required for webm audio decoding |
| Supabase project | Free tier works |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI API key | Used for both embeddings and TTS |
| Google Cloud project | Required for login (SSO) and Gmail / Calendar tools |

---

## Setup

### 1. Clone and configure environment

```bash
git clone <repo-url>
cd recall
cp .env.example .env
```

Edit `.env`:

```ini
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...          # used for embeddings + TTS
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
BACKEND_PORT=8000

# Google SSO — copy from your credentials.json
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
# (auto-generated on first run if omitted — stored in AppData/Local/Recall/)
JWT_SECRET=...

# Optional
OPENAI_TTS_VOICE=nova          # any OpenAI TTS voice: alloy, echo, fable, onyx, nova, shimmer
```

### 2. Initialize the database

Open your Supabase project → **SQL Editor** → paste the contents of `db/schema.sql` → **Run**.

This creates:
- `users` — Google identity and OAuth tokens
- `recall_items` — captured items with pgvector embeddings, due dates, and reminder state
- `sessions` — persisted conversation history across backend restarts

### 3. Set up the Python backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux
pip install -r requirements.txt
```

Start the server:

```bash
# From backend/ with venv active:
uvicorn main:app --reload --port 8000
```

Verify:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

### 4. Set up the Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create or open a project
2. Enable the **Gmail API** and **Google Calendar API**
3. Go to **APIs & Services → Credentials → Create credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
4. Under **Authorized redirect URIs**, add: `http://localhost:8000/auth/callback`
5. Download `credentials.json` and place it in `backend/`

### 5. Set up the frontend

```bash
cd app
pnpm install
pnpm tauri dev
```

First run compiles Rust — takes 2–5 minutes. Subsequent runs are fast.

On first launch the app shows a login screen. Click **Sign in with Google** — a browser window opens, you consent, and the app loads. The 30-day JWT is stored in localStorage; subsequent opens skip the login screen.

---

## Usage

Once both the backend and frontend are running:

### Floating orb (quick capture)
- Press `Ctrl+Shift+Space` to show/hide the orb window
- Speak — the orb records, transcribes, and responds
- The orb supports the full agent loop: tools, memory search, everything

### Main window — Agent tab
- Full streaming conversation with visible tool steps
- Email cards appear inline when the agent discusses specific emails
- Task cards appear inline when the agent surfaces open items
- Say **"any updates from my email?"** — agent reads inbox and summarizes
- Say **"what's on my calendar today?"** — agent lists events

### Reminders
- Say **"remind me to [x] at [time]"** — the agent stores it with a parsed due date
- The app delivers an audio reminder at the right time; if the window was closed, missed reminders appear as a yellow notification on next open

### Tasks tab
- Browse all stored recall items, filter by status, mark as resolved

### Profile tab
- Shows your Google account info and connection status
- Sign out button

---

## API reference

All endpoints except `/health` and `/auth/*` require an `Authorization: Bearer <token>` header.

### Auth endpoints

| Endpoint | Description |
|---|---|
| `GET /auth/url` | Returns `{ url, state }` — open `url` in system browser |
| `GET /auth/callback` | Google redirects here; exchanges code, issues JWT |
| `GET /auth/poll?state=` | Poll every 2 s; returns `{ ready, token }` when done |
| `GET /auth/me` | Validates JWT; returns `{ user_id, email, name }` |

### `POST /capture/stream`

Main endpoint. Accepts multipart form data, returns a Server-Sent Events stream.

| Field | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | yes | UUID identifying the conversation session |
| `audio` | file | one of | webm/opus audio from the microphone |
| `text` | string | one of | plain text to bypass STT (for programmatic use) |

SSE event types emitted:

| Event | Payload | Description |
|---|---|---|
| `transcript` | `{ text }` | STT result, streamed as soon as transcription completes |
| `thinking` | `{ text }` | Agent is reasoning |
| `tool_call` | `{ name, input }` | A tool was invoked |
| `tool_result` | `{ name, summary, data }` | Tool execution result |
| `ack_audio` | `{ audio_base64, text }` | Short acknowledgment audio, played immediately while tools run |
| `spoken` | `{ text }` | Final agent response text |
| `metadata` | `{ intent_type, should_store, due_hint, reminder_text }` | Classification result |
| `stored` | `{ item_id, due_at }` | Item stored in Supabase (`item_id` null if not stored) |
| `audio` | `{ audio_base64 }` | Final TTS response audio |
| `done` | — | Stream complete |

### `POST /capture`

Legacy single-response endpoint. Same form fields (audio required). Returns a single JSON object with `transcript`, `response_text`, `audio_base64`, `intent_type`, `item_id`, `due_at`.

### `GET /items`

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter by `open`, `resolved`, or `snoozed` |
| `has_due_hint` | bool | Only return items with a due date |
| `limit` | int | Max results, default 100 |

Results are scoped to the authenticated user.

### `PATCH /items/:id`

Update an item's status or due date. JSON body: `{ "status": "resolved" }` or `{ "due_hint": "tomorrow at 3pm" }`.

### `GET /reminders/pending`

Returns all unreminded future items for the authenticated user. No side effects.

### `GET /reminders/due`

Returns all currently-due items with synthesized TTS audio. Marks each as `reminded_at` only after TTS succeeds.

### `POST /reminders/dismiss`

Marks items as seen without delivering audio (used for missed reminders on startup). Body: `{ "ids": ["uuid", ...] }`.

---

## Intent types

| Type | Meaning | Stored? |
|---|---|---|
| `task` | Something to do | Yes |
| `blocker` | An impediment | Yes |
| `follow_up` | Something to check on later | Yes |
| `progress` | Update on existing work | Yes |
| `note` | General context, not actionable | Yes |
| `query` | A question about existing items | No |
| `update` | Changing the status of an existing item | No |

---

## Prompt caching

The Claude agent uses [Anthropic prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) on the system prompt. The system prompt is a stable module-level constant in `backend/agent.py` — date and RAG context are injected into the user turn only, so the cache is never invalidated between turns. Tool definitions are also cached. On `claude-sonnet-4-6`, cached tokens cost ~10% of the full input price.

---

## Testing

With the backend running:

```bash
python test_backend.py
```

Covers: backend health, items API, reminders API, intent classification, recall search, reminder due dates, file creation, Gmail and Calendar tools (auto-skipped if not configured), session memory, and item updates.

---

## Troubleshooting

**`STT failed` on first capture**
ffmpeg is not on PATH. Run `winget install Gyan.FFmpeg`, close and reopen your terminal.

**Microphone access denied**
Windows Settings → Privacy & security → Microphone → allow the app.

**`pnpm tauri dev` fails with linker error**
Rust is not installed. Install via [rustup.rs](https://rustup.rs), then restart your terminal.

**Supabase RPC error about `vector` type**
pgvector extension not enabled. Re-run `db/schema.sql` — the `CREATE EXTENSION IF NOT EXISTS vector` line handles this.

**Session persistence not working**
The `sessions` table doesn't exist. Run `db/schema.sql` in the Supabase SQL editor.

**Login screen shows "Backend unavailable"**
The FastAPI backend isn't running. Start it with `uvicorn main:app --reload --port 8000` from `backend/`.

**"opener.open_url not allowed" error**
The Tauri opener permission is missing. Ensure `capabilities/default.json` includes `"opener:default"` and `"opener:allow-open-url"`.

**Google OAuth error: redirect_uri mismatch**
`http://localhost:8000/auth/callback` is not listed in your OAuth client's authorized redirect URIs. Add it in Google Cloud Console → APIs & Services → Credentials → edit your OAuth 2.0 client.

**Gmail / Calendar tools not available after sign-in**
Check that the Gmail API and Google Calendar API are enabled in your Google Cloud project. The consent screen must include those scopes — sign out from the Profile tab and sign in again to re-consent.

**JWT expired (401 on all requests)**
The 30-day JWT has expired. Sign out from the Profile tab and sign in again, or clear `recall_auth_token` from localStorage.

**Window doesn't appear on `Ctrl+Shift+Space`**
Another app has claimed that shortcut. Change `HOTKEY` in `app/src/components/OrbWindow.tsx`.

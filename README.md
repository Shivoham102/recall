# Recall

Voice-first AI memory assistant. Speak a task, note, or question — Recall transcribes it, classifies it, stores it, and responds via voice.

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
                   │ Authorization: Bearer <Supabase JWT>
┌──────────────────▼───────────────────────────────────────────┐
│  FastAPI backend (localhost:8000)                             │
│                                                              │
│  POST /capture/stream   audio or text → SSE event stream     │
│  POST /capture          audio → single JSON response (legacy)│
│  GET  /items            list stored recall items             │
│  PATCH /items/:id       update item status / due date        │
│  GET  /reminders/pending  undelivered future reminders       │
│  GET  /reminders/due      due now → delivers TTS audio       │
│  POST /reminders/dismiss  mark missed reminders as seen      │
│  GET  /auth/callback    Supabase deep-link redirect page     │
│                                                              │
│  Agent loop (Claude claude-sonnet-4-6 + tool use):           │
│    classify_intent · recall_search · recall_update_item      │
│    surface_tasks · file_create                               │
│    gmail_get_updates · surface_cards · gmail_find_contact    │
│    gmail_fetch_style_samples · gmail_draft                   │
│    calendar_list · calendar_create                           │
│                                                              │
│  Cartesia   STT (ink-whisper) + TTS (sonic-2)                │
│  OpenAI     embeddings (text-embedding-3-small)              │
└──────────────────┬───────────────────────────────────────────┘
                   │ pgvector + JSONB
┌──────────────────▼───────────────────────────────────────────┐
│  Supabase (PostgreSQL + pgvector)                            │
│  Auth         — Google OAuth SSO via Supabase Auth           │
│  recall_items — items, embeddings, due dates, reminders      │
│  sessions     — persisted conversation history per session   │
└──────────────────────────────────────────────────────────────┘
```

**Streaming capture flow:** hold mic → webm/opus recorded in browser → `POST /capture/stream` → Cartesia STT transcribes → RAG retrieves similar items → Claude agentic loop with tools → SSE yields transcript, tool steps, spoken text, and TTS audio in real time → item stored if actionable.

**Auth flow:** app opens → checks Supabase session → if missing, shows login screen → user clicks "Sign in with Google" → Supabase opens browser OAuth → user consents → tokens return via deep link (`recall://auth#...`) → app parses tokens into Supabase session → renders main app.

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri v2 |
| Frontend | React 19 + TypeScript + Vite |
| Backend | FastAPI + Python 3.11+ |
| Auth | Supabase Auth (Google OAuth SSO) |
| STT | Cartesia `ink-whisper` |
| TTS | Cartesia `sonic-2` |
| Agent | Anthropic Claude `claude-sonnet-4-6` with prompt caching |
| Embeddings | OpenAI `text-embedding-3-small` (1536 dims) |
| Database | Supabase — PostgreSQL + pgvector (HNSW index) |
| Google APIs | Gmail + Google Calendar (optional) |

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
│   ├── auth.py               ← Supabase JWT validation, get_current_user dependency
│   ├── context.py            ← ContextVar for per-request user_id propagation
│   ├── session_store.py      ← Supabase-backed session persistence
│   ├── rag.py                ← embed(), retrieve_similar(), store_item()
│   ├── stt.py                ← Cartesia ink-whisper transcription
│   ├── tts.py                ← Cartesia sonic-2 synthesis
│   ├── db.py                 ← Supabase client
│   ├── google_auth.py        ← get_credentials_for_user() reads tokens from Supabase
│   ├── routes/
│   │   ├── voice.py          ← GET /auth/callback (Supabase deep-link redirect)
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
        │   ├── LoginScreen.tsx     ← Supabase Google SSO login
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
        │   └── useAuth.ts          ← Supabase session, auth header, logout
        └── services/
            ├── api.ts                  ← captureStream(), items, reminders API
            ├── supabase.ts             ← Supabase client singleton
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
| OpenAI API key | Used for embeddings |
| Cartesia API key | [cartesia.ai](https://cartesia.ai) — STT + TTS |

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
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
BACKEND_PORT=8000
CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=a0e99841-438c-4a64-b679-ae501e7d6091  # find voices at cartesia.ai/voices
```

### 2. Initialize the database

Open your Supabase project → **SQL Editor** → paste the contents of `db/schema.sql` → **Run**.

This creates:
- `recall_items` — captured items with pgvector embeddings, due dates, and reminder state
- `sessions` — persisted conversation history across backend restarts

Enable Google OAuth in Supabase: **Authentication → Providers → Google** — add your Google OAuth client ID and secret, and set the callback URL shown there in Google Cloud Console.

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
uvicorn main:app --reload --port 8000
```

Verify:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

### 4. Set up the frontend

```bash
cd app
pnpm install
pnpm tauri dev
```

First run compiles Rust — takes 2–5 minutes. Subsequent runs are fast.

On first launch the app shows a login screen. Click **Sign in with Google** — a browser window opens, you consent, and the app loads.

### 5. Optional: Google tools (Gmail + Calendar)

To enable inbox reading, email drafting, and calendar tools:

1. Create a Google Cloud project and enable the **Gmail API** and **Google Calendar API**
2. Go to **APIs & Services → Credentials → Create credentials → OAuth 2.0 Client ID** (Desktop app)
3. Download `credentials.json` and place it in `backend/`
4. Sign out and sign in again — the new consent screen includes Gmail + Calendar scopes

---

## Usage

### Floating orb (quick capture)
- Press `Ctrl+Shift+Space` to show/hide the orb window
- Speak — the orb records, transcribes, and responds
- Supports the full agent loop: tools, memory search, everything

### Main window — Agent tab
- Full streaming conversation with visible tool steps
- Email cards appear inline when the agent discusses specific emails
- Task cards appear inline when the agent surfaces open items
- Say **"any updates from my email?"** — agent reads inbox and summarizes
- Say **"what's on my calendar today?"** — agent lists events

### Reminders
- Say **"remind me to [x] at [time]"** — the agent stores it with a parsed due date
- The app delivers an audio reminder at the right time; missed reminders appear on next open

### Tasks tab
- Browse all stored recall items, filter by status, mark as resolved

---

## API reference

All endpoints except `/health` and `/auth/callback` require an `Authorization: Bearer <token>` header (Supabase JWT from `supabase.auth.getSession()`).

### `POST /capture/stream`

Main endpoint. Accepts multipart form data, returns a Server-Sent Events stream.

| Field | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | yes | UUID identifying the conversation session |
| `audio` | file | one of | webm/opus audio from the microphone |
| `text` | string | one of | plain text to bypass STT |

SSE event types emitted:

| Event | Payload | Description |
|---|---|---|
| `transcript` | `{ text }` | STT result |
| `thinking` | `{ text }` | Agent is reasoning |
| `tool_call` | `{ name, input }` | A tool was invoked |
| `tool_result` | `{ name, summary, data }` | Tool execution result |
| `ack_audio` | `{ audio_base64, text }` | Short acknowledgment audio, played immediately |
| `spoken` | `{ text }` | Final agent response text |
| `metadata` | `{ intent_type, should_store, due_hint, reminder_text }` | Classification result |
| `stored` | `{ item_id, due_at }` | Item stored (`item_id` null if not stored) |
| `audio` | `{ audio_base64 }` | Final TTS response audio (MP3, base64) |
| `done` | — | Stream complete |

### `GET /items`

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter by `open`, `resolved`, or `snoozed` |
| `has_due_hint` | bool | Only return items with a due date |
| `limit` | int | Max results, default 100 |

### `PATCH /items/:id`

JSON body: `{ "status": "resolved" }` or `{ "due_hint": "tomorrow at 3pm" }`.

### `GET /reminders/due`

Returns currently-due items with synthesized TTS audio. Marks each as `reminded_at` after TTS succeeds.

### `POST /reminders/dismiss`

Marks items as seen without audio. Body: `{ "ids": ["uuid", ...] }`.

---

## Intent types

| Type | Stored? | Meaning |
|---|---|---|
| `task` | Yes | Something to do |
| `blocker` | Yes | An impediment |
| `follow_up` | Yes | Something to check on later |
| `progress` | Yes | Update on existing work |
| `note` | Yes | General context, not actionable |
| `query` | No | A question about existing items |
| `update` | No | Changing status of an existing item |

---

## Prompt caching

The Claude agent uses [Anthropic prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) on the system prompt. The system prompt is a stable module-level constant in `backend/agent.py` — date and RAG context are injected into the user turn only, keeping the cache warm across turns. On `claude-sonnet-4-6`, cached tokens cost ~10% of the full input price.

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

**Gmail / Calendar tools not available after sign-in**
Check that the Gmail API and Google Calendar API are enabled in your Google Cloud project. Sign out from the Profile tab and sign in again to re-consent with the new scopes.

**Window doesn't appear on `Ctrl+Shift+Space`**
Another app has claimed that shortcut. Change `HOTKEY` in `app/src/components/OrbWindow.tsx`.

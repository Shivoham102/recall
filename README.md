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

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Tauri v2 + React (two windows)                              │
│                                                              │
│  FloatingWindow — Ctrl+Shift+Space, quick voice capture      │
│  MainApp — 4 tabs: Agent · Tasks · Transcripts · Reminders   │
│  System tray icon for minimize/restore                       │
└──────────────────┬───────────────────────────────────────────┘
                   │ HTTP + Server-Sent Events (SSE)
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
│  ElevenLabs      TTS voice response                          │
└──────────────────┬───────────────────────────────────────────┘
                   │ pgvector + JSONB
┌──────────────────▼───────────────────────────────────────────┐
│  Supabase (PostgreSQL + pgvector)                            │
│  recall_items — items, embeddings, due dates, reminders      │
│  sessions     — persisted conversation history per session   │
└──────────────────────────────────────────────────────────────┘
```

**Streaming capture flow:** hold mic → webm/opus recorded in browser → `POST /capture/stream` → faster-whisper transcribes → RAG retrieves similar items → Claude agentic loop with tools → SSE yields transcript, tool steps, spoken text, and TTS audio in real time → item stored if actionable.

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri v2 |
| Frontend | React 19 + TypeScript + Vite |
| Backend | FastAPI + Python 3.11+ |
| STT | faster-whisper (local, `base` model, CPU) |
| TTS | ElevenLabs API (`eleven_turbo_v2`) |
| Agent | Anthropic Claude `claude-sonnet-4-6` with prompt caching |
| Embeddings | OpenAI `text-embedding-3-small` (1536 dims) |
| Database | Supabase — PostgreSQL + pgvector (HNSW index) |
| Google APIs | Gmail + Google Calendar (optional, OAuth 2.0) |

---

## Project structure

```
recall/
├── .env.example
├── db/
│   └── schema.sql            ← run in Supabase SQL editor (recall_items + sessions tables)
├── test_backend.py           ← end-to-end test script
├── backend/
│   ├── main.py               ← FastAPI app + route registration
│   ├── agent.py              ← Claude sessions, agentic loop, prompt caching
│   ├── session_store.py      ← Supabase-backed session persistence
│   ├── rag.py                ← embed(), retrieve_similar(), store_item()
│   ├── stt.py                ← faster-whisper transcription
│   ├── tts.py                ← ElevenLabs synthesis
│   ├── db.py                 ← Supabase client
│   ├── google_auth.py        ← OAuth2 flow for Gmail + Calendar
│   ├── routes/
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
        │   ├── FloatingWindow.tsx  ← orb window (Ctrl+Shift+Space)
        │   ├── VoiceButton.tsx
        │   ├── ChatHistory.tsx
        │   └── tabs/
        │       ├── AgentTab.tsx        ← streaming chat, tool steps, email/task cards
        │       ├── TasksTab.tsx
        │       ├── TranscriptsTab.tsx
        │       └── RemindersTab.tsx
        ├── hooks/
        │   └── useRecorder.ts
        └── services/
            ├── api.ts                  ← captureStream(), items, reminders API
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
| OpenAI API key | For embeddings only |
| ElevenLabs API key | [elevenlabs.io](https://elevenlabs.io) |
| Google Cloud project | Optional — only needed for Gmail / Calendar tools |

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
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # default: Rachel
BACKEND_PORT=8000
```

### 2. Initialize the database

Open your Supabase project → **SQL Editor** → paste the contents of `db/schema.sql` → **Run**.

This creates:
- `recall_items` — stores captured items with pgvector embeddings, due dates, and reminder state
- `sessions` — persists conversation history across backend restarts

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

### 4. Set up the frontend

```bash
cd app
pnpm install
pnpm tauri dev
```

First run compiles Rust — takes 2–5 minutes. Subsequent runs are fast.

### 5. Google integration (optional)

To enable Gmail and Google Calendar tools:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com)
2. Enable the **Gmail API** and **Google Calendar API**
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download `credentials.json` and place it in `backend/`
5. Run the auth flow once:
   ```bash
   cd backend && python google_auth.py
   ```
   A browser window will open — sign in and grant access. This saves `token.json` and is not needed again unless the token expires.

The app works fully without Google credentials — Gmail and Calendar tools are simply unavailable.

---

## Usage

Once both the backend and frontend are running:

### Floating orb (quick capture)
- Press `Ctrl+Shift+Space` to show/hide the orb window
- Hold the mic button, speak, release — done in ~10 seconds
- The orb supports the full agent loop: tools, memory search, everything

### Main window — Agent tab
- Full streaming conversation with visible tool steps
- Email cards appear inline when the agent discusses specific emails
- Task cards appear inline when the agent surfaces open items
- Say **"any updates from my email?"** — agent reads inbox and summarizes
- Say **"what's on my calendar today?"** — agent lists events
- Say **"brief me"** — combined email + calendar + tasks morning summary

### Reminders
- Say **"remind me to [x] at [time]"** — the agent stores it with a parsed due date
- The app delivers an audio reminder at the right time; if the window was closed, missed reminders appear as a yellow notification on next open

### Tasks tab
- Browse all stored recall items, filter by status, mark as resolved

---

## API reference

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
| `stored` | `{ item_id, due_at }` | Item stored in Supabase (item_id null if not stored) |
| `audio` | `{ audio_base64 }` | Final TTS response audio |
| `done` | — | Stream complete |

### `POST /capture`

Legacy single-response endpoint (used by the original orb path). Same form fields as above (audio required). Returns a single JSON object with `transcript`, `response_text`, `audio_base64`, `intent_type`, `item_id`, `due_at`.

### `GET /items`

Query stored recall items.

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter by `open`, `resolved`, or `snoozed` |
| `has_due_hint` | bool | Only return items with a due date |
| `limit` | int | Max results, default 100 |

### `PATCH /items/:id`

Update an item's status or due date. JSON body: `{ "status": "resolved" }` or `{ "due_hint": "tomorrow at 3pm" }`.

### `GET /reminders/pending`

Returns all items with a due date that haven't been delivered yet. No side effects.

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

**ElevenLabs returns 401**
`ELEVENLABS_API_KEY` in `.env` is missing or incorrect.

**Supabase RPC error about `vector` type**
pgvector extension not enabled. Re-run `db/schema.sql` — the `CREATE EXTENSION IF NOT EXISTS vector` line handles this.

**Session persistence not working**
The `sessions` table doesn't exist. Run `db/schema.sql` in the Supabase SQL editor (the `CREATE TABLE IF NOT EXISTS sessions` statement at the top).

**Gmail / Calendar tools not available**
`credentials.json` is missing from `backend/`, or `python google_auth.py` hasn't been run yet. The app works without them — only those tools are unavailable.

**`token.json` expired / Google auth error**
Delete `backend/token.json` and run `python google_auth.py` again to re-authenticate.

**Window doesn't appear on `Ctrl+Shift+Space`**
Another app has claimed that shortcut. Change `HOTKEY` in `app/src/components/FloatingWindow.tsx`.

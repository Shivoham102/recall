# Recall

A conversational voice assistant for managing working memory throughout the day. Not a logger you talk at — an agent you talk with.

Hit a hotkey, speak a thought, and Recall captures it, classifies it, and responds. Ask it what's open, what you got done today, or what you said you'd follow up on — it answers from everything you've told it, with full context.

---

## What it does

- **Quick capture** — global hotkey, one sentence, done in 10 seconds
- **Thinking partner** — multi-turn voice conversation, end-of-day review, untangling what's open
- **Intent classification** — automatically tags each input as a task, blocker, follow-up, progress update, or note
- **RAG-powered memory** — before responding, the agent retrieves semantically similar items from your history and reasons over their status and timestamps
- **Conversational clarification** — if input is ambiguous, the agent asks one follow-up question before storing

---

## Architecture

```
┌─────────────────────────────────────────┐
│  Tauri v2 + React (floating window)     │
│  Ctrl+Shift+Space to show/hide          │
│  Hold mic button → speak → release      │
└───────────────┬─────────────────────────┘
                │ HTTP (multipart / JSON)
┌───────────────▼─────────────────────────┐
│  FastAPI backend (localhost:8000)        │
│                                         │
│  POST /capture  audio → full pipeline   │
│  POST /query    text  → retrieve+reply  │
│                                         │
│  faster-whisper  STT (local, CPU)       │
│  OpenAI          embeddings             │
│  Claude          agent + classification │
│  ElevenLabs      TTS voice response     │
└───────────────┬─────────────────────────┘
                │ pgvector
┌───────────────▼─────────────────────────┐
│  Supabase (PostgreSQL + pgvector)        │
│  recall_items table + HNSW index         │
└─────────────────────────────────────────┘
```

**Capture flow:** hold mic → webm/opus recorded in browser → POST /capture → faster-whisper transcribes → OpenAI embeds → Supabase retrieves similar open items → Claude classifies intent + generates response with context → ElevenLabs synthesizes → base64 MP3 played back → item stored if actionable.

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop shell | Tauri v2 |
| Frontend | React 19 + TypeScript + Vite |
| Backend | FastAPI + Python 3.11+ |
| STT | faster-whisper (local, `base` model) |
| TTS | ElevenLabs API (`eleven_turbo_v2`) |
| Agent | Anthropic Claude (`claude-sonnet-4-6`) with prompt caching |
| Embeddings | OpenAI `text-embedding-3-small` (1536 dims) |
| Database | Supabase — PostgreSQL + pgvector (HNSW index) |

---

## Project structure

```
recall/
├── .env.example          ← copy to .env and fill in API keys
├── db/
│   └── schema.sql        ← run this in Supabase SQL editor first
├── backend/
│   ├── main.py           ← FastAPI app entry
│   ├── agent.py          ← Claude session management + prompt caching
│   ├── rag.py            ← embeddings + vector retrieval/storage
│   ├── stt.py            ← faster-whisper transcription
│   ├── tts.py            ← ElevenLabs synthesis
│   ├── db.py             ← Supabase client
│   ├── routes/
│   │   ├── capture.py    ← POST /capture
│   │   └── query.py      ← POST /query
│   ├── requirements.txt
│   └── .venv/            ← Python virtual environment
└── app/                  ← Tauri + React frontend
    ├── src/
    │   ├── components/
    │   │   ├── FloatingWindow.tsx
    │   │   ├── VoiceButton.tsx
    │   │   └── ChatHistory.tsx
    │   ├── hooks/
    │   │   └── useRecorder.ts
    │   └── services/
    │       └── api.ts
    └── src-tauri/        ← Rust/Tauri configuration
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
| OpenAI API key | For embeddings only (`text-embedding-3-small`) |
| ElevenLabs API key | [elevenlabs.io](https://elevenlabs.io) |

---

## Setup

### 1. Clone and configure environment

```bash
git clone <repo-url>
cd recall
cp .env.example .env
```

Edit `.env` and fill in all values:

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

This creates the `recall_items` table with a pgvector column, an HNSW index, and the `match_recall_items` similarity search function.

### 3. Set up the Python backend

```bash
cd backend
python -m venv .venv           # skip if already exists
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux
pip install -r requirements.txt
```

Copy the `.env` file from the project root into `backend/` as well, or run the server from the root so python-dotenv can find it:

```bash
# From the project root:
backend\.venv\Scripts\python backend\main.py
```

Or activate the venv and run from the backend directory directly:

```bash
cd backend && .venv\Scripts\activate
python main.py
```

Verify it's running:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

### 4. Set up and run the frontend

```bash
cd app
pnpm install       # skip if already done
pnpm tauri dev
```

The first `tauri dev` compiles Rust — this takes 2–5 minutes. Subsequent runs are fast.

---

## Usage

Once both the backend and frontend are running:

1. **Show/hide the window** — press `Ctrl+Shift+Space` from anywhere
2. **Capture something** — hold the mic button, speak, release
3. **Query your memory** — hold the button and say something like:
   - *"What's still open?"*
   - *"What did I work on today?"*
   - *"What did I say I'd follow up on?"*
4. **Dismiss the window** — press `Ctrl+Shift+Space` again, or click ✕

The agent responds by voice and shows the transcript + response in the window. Each captured item is stored with its embedding in Supabase and retrieved semantically on future interactions.

---

## API reference

### `POST /capture`

Accepts multipart form data. Transcribes audio, classifies intent, retrieves context, generates and speaks a response, stores actionable items.

| Field | Type | Description |
|---|---|---|
| `audio` | file | webm/opus or wav audio |
| `session_id` | string | UUID identifying the conversation session |

Response:
```json
{
  "transcript": "I need to fix the login bug before the demo",
  "response_text": "Got it, I've logged that as a task.",
  "audio_base64": "<base64 MP3>",
  "intent_type": "task",
  "item_id": "uuid-or-null"
}
```

### `POST /query`

Accepts JSON. For text-only queries (no audio recording needed).

```json
{ "text": "what is open?", "session_id": "your-session-uuid" }
```

Response:
```json
{
  "response_text": "You have 3 open items: ...",
  "audio_base64": "<base64 MP3>",
  "items": [...]
}
```

### `GET /health`

Returns `{"status": "ok"}`.

---

## Intent types

Claude classifies each input into one of:

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

The Claude agent uses [Anthropic prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) on the system prompt. The system prompt is a stable module-level constant in `backend/agent.py` — date and RAG context are injected into the user turn instead, so the cache is never invalidated between calls in a session. On `claude-sonnet-4-6`, cached tokens cost ~10% of the full input price, which adds up quickly for a voice assistant with many turns per session.

---

## Troubleshooting

**`STT failed: ...` on first capture**
ffmpeg is not on PATH. Run `winget install Gyan.FFmpeg`, close and reopen your terminal.

**Microphone access denied**
Windows Settings → Privacy & security → Microphone → allow the app.

**`pnpm tauri dev` fails with linker error**
Rust is not installed. Install via [rustup.rs](https://rustup.rs), then restart your terminal.

**ElevenLabs returns 401**
`ELEVENLABS_API_KEY` in `.env` is missing or incorrect.

**Supabase RPC returns an error about `vector` type**
The pgvector extension wasn't enabled. Re-run `db/schema.sql` — the `CREATE EXTENSION IF NOT EXISTS vector` line at the top handles this.

**Window doesn't appear on `Ctrl+Shift+Space`**
Another app may have claimed that shortcut. Change `HOTKEY` in `app/src/components/FloatingWindow.tsx` to something else (e.g. `Ctrl+Shift+R`).

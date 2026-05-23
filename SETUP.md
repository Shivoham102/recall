# Setup

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python --version` |
| Node.js 18+ | `node --version` |
| pnpm | `npm install -g pnpm` |
| Rust + Cargo | [rustup.rs](https://rustup.rs) (required for Tauri) |
| ffmpeg | `winget install Gyan.FFmpeg` (required for webm audio decoding) |
| Supabase project | Free tier works |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI API key | Used for embeddings |
| Cartesia API key | [cartesia.ai](https://cartesia.ai) (STT + TTS) |
| Supermemory API key | Optional, personal memory/profile integration |

---

## 1. Clone and configure environment

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
SUPABASE_SERVICE_ROLE_KEY=eyJ...
BACKEND_PORT=8000
CRON_SECRET=change-me
CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=a0e99841-438c-4a64-b679-ae501e7d6091
# Optional
SUPERMEMORY_API_KEY=...
SUPERMEMORY_ENABLED=true
```

---

## 2. Initialize the database

Open your Supabase project -> **SQL Editor** -> paste `db/schema.sql` -> **Run**.

Creates: `recall_items`, `sessions`, `email_style_profiles`, `email_style_events`.

Enable Google OAuth: **Authentication -> Providers -> Google**, add your OAuth client ID and secret, and copy the callback URL shown there into Google Cloud Console.

---

## 3. Python backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Verify: `curl http://localhost:8000/health` -> `{"status":"ok"}`

---

## 4. Frontend

```bash
cd app
pnpm install
pnpm tauri dev
```

First run compiles Rust (2-5 min). On first launch, click **Sign in with Google**.

---

## 5. Google tools (Gmail + Calendar)

1. Create a Google Cloud project, enable the **Gmail API** and **Google Calendar API**
2. **APIs & Services -> Credentials -> Create credentials -> OAuth 2.0 Client ID** (Desktop app)
3. Download `credentials.json`, place it in `backend/`
4. Sign out and sign in again to re-consent with the new scopes

---

## 6. Weekly style profile cron (Vercel)

`vercel.json` schedules `GET /jobs/refresh-email-style-profiles` weekly for low-latency personalized email drafting.

Vercel env vars required: `CRON_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`.

Local dev works without cron (draft-time fallback refresh kicks in automatically).

---

## 7. Supermemory (optional)

Set `SUPERMEMORY_API_KEY` to enable the Memory tab and personalization context.
Omit the key or set `SUPERMEMORY_ENABLED=false` to skip. Recall degrades gracefully either way.

---

## Usage

### Orb (quick capture)
- `Ctrl+Shift+Space`: show/hide the orb
- Speak: records, transcribes, responds

### Agent tab
- Full streaming conversation with visible tool steps
- Email and task cards appear inline
- *"Any updates from my email?"*, *"What's on my calendar today?"*

### Reminders
- *"Remind me to [x] at [time]"*: stored with parsed due date, delivered as audio

### Tasks tab
- Browse all items, filter by status, mark as resolved

---

## API reference

All endpoints except `/health` and `/auth/callback` require `Authorization: Bearer <Supabase JWT>`.

### `POST /capture/stream`

Multipart form -> Server-Sent Events stream.

| Field | Type | Description |
|---|---|---|
| `session_id` | string | UUID for the conversation |
| `audio` | file | webm/opus from microphone |
| `text` | string | plain text (bypasses STT) |

SSE events:

| Event | Payload | Description |
|---|---|---|
| `transcript` | `{ text }` | STT result |
| `tool_call` | `{ name, input }` | Tool invoked |
| `tool_result` | `{ name, summary, data }` | Tool result |
| `ack_audio` | `{ audio_base64, text }` | Short acknowledgment audio |
| `spoken` | `{ text }` | Final agent response |
| `metadata` | `{ intent_type, should_store, due_hint }` | Classification |
| `stored` | `{ item_id, due_at }` | Item stored (null if not stored) |
| `audio` | `{ audio_base64 }` | Final TTS audio (MP3, base64) |
| `done` | (none) | Stream complete |

### `GET /items`

| Param | Type | Description |
|---|---|---|
| `status` | string | `open`, `resolved`, or `snoozed` |
| `has_due_hint` | bool | Only items with a due date |
| `limit` | int | Max results (default 100) |

### `PATCH /items/:id`

Body: `{ "status": "resolved" }` or `{ "due_hint": "tomorrow at 3pm" }`.

### `GET /reminders/due`

Returns due items with synthesized TTS audio. Marks each as `reminded_at`.

### `POST /reminders/dismiss`

Body: `{ "ids": ["uuid", ...] }`. Marks items seen without audio.

---

## Intent types

| Type | Stored? | Meaning |
|---|---|---|
| `task` | Yes | Something to do |
| `blocker` | Yes | An impediment |
| `follow_up` | Yes | Something to check on later |
| `progress` | Yes | Update on existing work |
| `note` | Yes | General context, not actionable |
| `query` | No | Question about existing items |
| `update` | No | Status change for existing item |

---

## Testing

With backend running:

```bash
python test_backend.py
```

Covers: health, items API, reminders, intent classification, RAG search, file creation, Gmail/Calendar (auto-skipped if not configured), session memory, item updates.

---

## Troubleshooting

**`STT failed` on first capture**
ffmpeg not on PATH. Run `winget install Gyan.FFmpeg`, restart terminal.

**Microphone access denied**
Windows Settings -> Privacy & security -> Microphone -> allow the app.

**`pnpm tauri dev` fails with linker error**
Rust not installed. Install via [rustup.rs](https://rustup.rs), restart terminal.

**Supabase RPC error about `vector` type**
pgvector not enabled. Re-run `db/schema.sql` (`CREATE EXTENSION IF NOT EXISTS vector` handles this).

**Session persistence not working**
`sessions` table missing. Run `db/schema.sql` in the Supabase SQL editor.

**Login screen shows "Backend unavailable"**
Backend not running. Start with `uvicorn main:app --reload --port 8000` from `backend/`.

**Gmail / Calendar tools missing after sign-in**
Confirm Gmail and Calendar APIs are enabled in Google Cloud. Sign out and sign in again to re-consent.

**`Ctrl+Shift+Space` doesn't open the orb**
Another app claimed the shortcut. Change `HOTKEY` in [app/src/components/OrbWindow.tsx](app/src/components/OrbWindow.tsx).

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Users (Google SSO) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
  id                    TEXT PRIMARY KEY,  -- Google sub
  email                 TEXT NOT NULL,
  name                  TEXT,
  google_access_token   TEXT,
  google_refresh_token  TEXT,
  google_token_expiry   TIMESTAMPTZ,
  created_at            TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now()
);

-- ── Sessions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
  session_id  text        PRIMARY KEY,
  history     jsonb       NOT NULL DEFAULT '[]',
  user_id     text        REFERENCES users(id),
  updated_at  timestamptz DEFAULT now()
);

-- ── Recall items ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recall_items (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  content     text        NOT NULL,
  embedding   vector(1536),
  intent_type text,
  status      text        DEFAULT 'open',
  created_at  timestamptz DEFAULT now(),
  updated_at  timestamptz DEFAULT now(),
  due_hint    text,
  due_at      timestamptz,
  reminder_text text,
  reminded_at   timestamptz,
  user_id     text        REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS recall_items_embedding_idx ON recall_items USING hnsw (embedding vector_cosine_ops);

-- ── Email style profiles (weekly personalization cache) ───────────────────────
CREATE TABLE IF NOT EXISTS email_style_profiles (
  user_id            text PRIMARY KEY REFERENCES users(id),
  sample_count       int NOT NULL DEFAULT 0,
  samples_preview    text NOT NULL DEFAULT '',
  style_features     jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_refreshed_at  timestamptz,
  next_refresh_at    timestamptz,
  updated_at         timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS email_style_events (
  id          bigserial PRIMARY KEY,
  user_id     text REFERENCES users(id),
  event_type  text NOT NULL,
  details     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz DEFAULT now()
);

-- ── Agent chats (UI thread persistence) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_chats (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_session_id text NOT NULL,
  title            text,
  turns            jsonb NOT NULL DEFAULT '[]'::jsonb,
  last_capture     jsonb,
  archived_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, agent_session_id)
);

CREATE INDEX IF NOT EXISTS agent_chats_user_updated_idx
  ON agent_chats (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS agent_chats_user_active_idx
  ON agent_chats (user_id, updated_at DESC)
  WHERE archived_at IS NULL;

ALTER TABLE agent_chats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_chats_select_own ON agent_chats;
CREATE POLICY agent_chats_select_own ON agent_chats
  FOR SELECT USING ((auth.uid())::text = user_id);

DROP POLICY IF EXISTS agent_chats_insert_own ON agent_chats;
CREATE POLICY agent_chats_insert_own ON agent_chats
  FOR INSERT WITH CHECK ((auth.uid())::text = user_id);

DROP POLICY IF EXISTS agent_chats_update_own ON agent_chats;
CREATE POLICY agent_chats_update_own ON agent_chats
  FOR UPDATE USING ((auth.uid())::text = user_id);

DROP POLICY IF EXISTS agent_chats_delete_own ON agent_chats;
CREATE POLICY agent_chats_delete_own ON agent_chats
  FOR DELETE USING ((auth.uid())::text = user_id);

-- ── RAG search (filters by user_id when provided) ────────────────────────────
CREATE OR REPLACE FUNCTION match_recall_items(
  query_embedding vector(1536),
  match_count     int  DEFAULT 5,
  p_user_id       text DEFAULT NULL
)
RETURNS TABLE (
  id          uuid,
  content     text,
  intent_type text,
  status      text,
  created_at  timestamptz,
  due_hint    text,
  similarity  float
)
LANGUAGE sql STABLE AS $$
  SELECT
    id, content, intent_type, status, created_at, due_hint,
    1 - (embedding <-> query_embedding) AS similarity
  FROM recall_items
  WHERE status = 'open'
    AND (p_user_id IS NULL OR user_id = p_user_id)
  ORDER BY embedding <-> query_embedding
  LIMIT match_count;
$$;

-- ── Proactive agent jobs ──────────────────────────────────────────────────────
-- Tracks every proactive job run per user. Dedupe and retry logic lives in runner.py.
-- job_type: 'morning_brief' | 'email_triage' | 'follow_up_scan' | 'pattern_learn' | 'smart_reminder'
-- context_key: NULL for most jobs; stores item_id for smart_reminder per-item dedupe.
-- retry_count: counts failures only, not pre-execution attempts.
CREATE TABLE IF NOT EXISTS proactive_jobs (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_type     text        NOT NULL,
  context_key  text,
  status       text        NOT NULL DEFAULT 'running',
  result       jsonb,
  delivered    boolean     NOT NULL DEFAULT false,
  retry_count  int         NOT NULL DEFAULT 0,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  error        text
);

CREATE INDEX IF NOT EXISTS proactive_jobs_user_type_idx
  ON proactive_jobs (user_id, job_type, started_at DESC);

CREATE INDEX IF NOT EXISTS proactive_jobs_undelivered_idx
  ON proactive_jobs (user_id, delivered)
  WHERE delivered = false AND status = 'done';

-- ── Follow-up thread tracking ─────────────────────────────────────────────────
-- Tracks "I'll get back to them" commitments detected in email or voice.
CREATE TABLE IF NOT EXISTS follow_up_threads (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  thread_id       text,
  counterparty    text        NOT NULL,
  commitment_text text        NOT NULL,
  detected_at     timestamptz NOT NULL DEFAULT now(),
  due_hint        text,
  due_at          timestamptz,
  nudge_count     int         NOT NULL DEFAULT 0,
  last_nudged_at  timestamptz,
  status          text        NOT NULL DEFAULT 'open',
  source          text        NOT NULL DEFAULT 'email',
  recall_item_id  uuid        REFERENCES recall_items(id)
);

CREATE INDEX IF NOT EXISTS follow_up_threads_user_status_idx
  ON follow_up_threads (user_id, status, due_at);

-- ── User behavior patterns ────────────────────────────────────────────────────
-- Stores repeated query patterns extracted by the nightly pattern_learn job.
-- Promoted to auto_run=true when frequency >= 3 AND confidence >= 0.8.
CREATE TABLE IF NOT EXISTS user_behavior_patterns (
  id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pattern_type   text        NOT NULL,
  query_template text,
  frequency      int         NOT NULL DEFAULT 1,
  auto_run       boolean     NOT NULL DEFAULT false,
  confidence     float       NOT NULL DEFAULT 0.0,
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, pattern_type, query_template)
);

-- ── Users: proactive preferences + checkin tracking ───────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS timezone               text    DEFAULT 'UTC',
  ADD COLUMN IF NOT EXISTS proactive_morning_brief boolean DEFAULT true,
  ADD COLUMN IF NOT EXISTS morning_brief_hour     int     DEFAULT 7,
  ADD COLUMN IF NOT EXISTS last_morning_brief_at  timestamptz,
  ADD COLUMN IF NOT EXISTS last_checkin_at        timestamptz;

-- ── Agent chats: proactive inbox flag ────────────────────────────────────────
ALTER TABLE agent_chats
  ADD COLUMN IF NOT EXISTS is_proactive_inbox boolean NOT NULL DEFAULT false;

-- Ensures at most one proactive inbox chat per user.
CREATE UNIQUE INDEX IF NOT EXISTS agent_chats_proactive_inbox_per_user
  ON agent_chats (user_id) WHERE is_proactive_inbox = true;

-- ── Migration helpers (run once after SSO is set up) ─────────────────────────
-- Add user_id to existing tables if upgrading from schema without it:
--   ALTER TABLE recall_items ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id);
--   ALTER TABLE sessions     ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id);
--
-- Delete old dev rows that have no user_id:
--   DELETE FROM recall_items WHERE user_id IS NULL;
--   DELETE FROM sessions     WHERE user_id IS NULL;

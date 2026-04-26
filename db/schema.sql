CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE recall_items (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  content     text        NOT NULL,
  embedding   vector(1536),
  intent_type text,
  status      text        DEFAULT 'open',
  created_at  timestamptz DEFAULT now(),
  updated_at  timestamptz DEFAULT now(),
  due_hint    text
);

CREATE INDEX ON recall_items USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION match_recall_items(
  query_embedding vector(1536),
  match_count     int DEFAULT 5
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
  ORDER BY embedding <-> query_embedding
  LIMIT match_count;
$$;

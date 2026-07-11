-- llm-wiki SQLite schema
-- Applied with: sqlite3 db/wiki.db < db/schema.sql
-- (db/wiki.db itself is gitignored -- this file is the source of truth.)

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per raw/ document the normalizers have produced or the ingest
-- pipeline has processed. Keyed on content_hash so re-running a normalizer
-- against an updated export dump, or re-running ingest.py, is a no-op for
-- anything already seen.
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_path        TEXT NOT NULL UNIQUE,          -- repo-relative path under raw/
    source_type     TEXT NOT NULL,                 -- 'claude_export' | 'chatgpt_export' | 'article' | 'other'
    external_id     TEXT,                          -- upstream conversation id, if any (used for dedup on re-export)
    content_hash    TEXT NOT NULL,                 -- sha256 of normalized content, used for idempotent re-runs
    normalized_at   TEXT,                          -- ISO 8601 UTC, set by normalize_*.py
    ingested_at     TEXT,                          -- ISO 8601 UTC, set by ingest.py once successfully processed
    ingest_status   TEXT NOT NULL DEFAULT 'pending',-- 'pending' | 'ingested' | 'error'
    ingest_error    TEXT,                          -- last error message, if ingest_status = 'error'
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sources_content_hash ON sources(content_hash);
CREATE INDEX IF NOT EXISTS idx_sources_external_id ON sources(source_type, external_id);
CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(ingest_status);

-- Every DeepSeek API call, across ingest/query/lint, for cost tracking.
-- Mirrors the batch_runner.py cost-logging pattern: log per-call, not
-- per-batch, so a crash mid-batch doesn't lose the spend record.
CREATE TABLE IF NOT EXISTS api_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    operation           TEXT NOT NULL,          -- 'ingest' | 'query' | 'lint'
    model_tier          TEXT NOT NULL,          -- 'deep' | 'quick'
    model_name          TEXT NOT NULL,          -- actual model string used, e.g. 'deepseek-v4-pro'
    source_ref          TEXT,                   -- raw/ path, wiki/ slug, or query text this call was for
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    total_tokens        INTEGER,
    estimated_cost_usd  REAL,
    success             INTEGER NOT NULL DEFAULT 1,  -- 0/1
    error                TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(ts);
CREATE INDEX IF NOT EXISTS idx_api_calls_operation ON api_calls(operation);

-- Per-document run record for ingest.py, separate from api_calls (one
-- ingest can involve multiple API calls). Lets lint.py and status tooling
-- ask "what pages did this source touch" without re-parsing log.md.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    ts              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    pages_created   TEXT,   -- JSON array of wiki/ relative paths
    pages_updated   TEXT,   -- JSON array of wiki/ relative paths
    status          TEXT NOT NULL,   -- 'ok' | 'error'
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_source ON ingest_runs(source_id);

-- SQLite FTS5 full-text index over wiki page bodies, used by query.py and
-- lint.py's dup-detection pass. Rebuilt by a maintenance step in ingest.py
-- (delete+reinsert the row for any page written this run) rather than kept
-- in perfect sync via triggers -- simpler, and ingest is the only writer.
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
    slug UNINDEXED,
    path UNINDEXED,
    title,
    body,
    tokenize = 'porter unicode61'
);

-- schema.sql — applied once on first launch via db.py
-- Increment PRAGMA user_version on every schema change; add a migration block in db.py.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
-- PRAGMA user_version = 4;

-- ─────────────────────────────────────────────────────────────────────────────
-- L0 index (truth lives in Markdown files; this table is the queryable index)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entries (
    id          TEXT PRIMARY KEY,                        -- stable UID, currently UUID v4
    date        TEXT NOT NULL,                           -- YYYY-MM-DD
    type        TEXT NOT NULL CHECK(type IN ('chat','journal')),
    text        TEXT NOT NULL,                           -- full turn/entry text
    word_count  INTEGER,
    is_seeded   INTEGER NOT NULL DEFAULT 0,              -- 1 = demo seed data; exclude from recall
    created_at  TEXT NOT NULL                            -- ISO-8601
);

-- Full-text search over raw entry text
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    text,
    content='entries',
    content_rowid='rowid'
);

-- ─────────────────────────────────────────────────────────────────────────────
-- L1 extractions — one row per entry; ALL fields the model must pull out
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS extractions (
    id                  TEXT PRIMARY KEY,
    entry_id            TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    extraction_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK(extraction_status IN ('pending','done','failed','null_stored')),
    source_hash         TEXT,              -- sha256 of the canonical L0 body used for extraction
    -- Mood scalar and emotion array
    mood                INTEGER,           -- -5..+5; NULL if extraction failed
    emotions            TEXT,              -- JSON: [{name, intensity: 0..1}]
    -- Structured facts (all JSON; NULL until extraction succeeds)
    entities            TEXT,              -- JSON: [{name, type: person|place|project, normalized}]
    themes              TEXT,              -- JSON: [string]
    events              TEXT,              -- JSON: [string]   — what actually happened
    stated_goals        TEXT,              -- JSON: [{text, is_new: bool}]
    behaviors           TEXT,              -- JSON: [string]   — what user actually did (distinct from goals)
    decisions           TEXT,              -- JSON: [string]
    open_loops          TEXT,              -- JSON: [{description, status: open|updated|resolved}]
    self_judgments      TEXT,              -- JSON: [string]   — regrets, self-criticism signals
    -- Summary for ChromaDB embedding (embedded at the same time extraction runs)
    summary             TEXT,             -- 4-5 sentences; NULL until status = done
    extracted_at        TEXT,             -- ISO-8601; NULL until status = done
    -- R8: 1 once this entry's `done` extraction has been folded into L3 by a
    -- consolidation run, so each entry contributes to the profile exactly once —
    -- regardless of when its (possibly late) extraction finished (v5).
    consolidated        INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_extractions_entry_id ON extractions(entry_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- L4 mood time-series (denormalized for fast chart queries; no LLM needed)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mood_series (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    mood        INTEGER,                   -- copied from extractions.mood
    emotions    TEXT,                      -- JSON copy from extractions.emotions
    is_seeded   INTEGER NOT NULL DEFAULT 0
);

-- ─────────────────────────────────────────────────────────────────────────────
-- L4 knowledge graph
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK(type IN ('theme','person','place','goal','problem','emotion')),
    entry_count INTEGER NOT NULL DEFAULT 0,
    entries     TEXT,                      -- JSON: [entry_id]
    is_seeded   INTEGER NOT NULL DEFAULT 0 -- 1 = demo seed data; pruned when the real L4 builder runs
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL REFERENCES graph_nodes(id),
    target       TEXT NOT NULL REFERENCES graph_nodes(id),
    type         TEXT NOT NULL CHECK(type IN ('co_occurrence','temporal','similarity','hypothesis')),
    weight       REAL NOT NULL DEFAULT 0.0,
    is_hypothesis INTEGER NOT NULL DEFAULT 0,   -- 1 = model-proposed; shown with confirm/dismiss UI
    label        TEXT,                           -- human-readable edge label (e.g. "may lead to")
    entries      TEXT,                           -- JSON: [entry_id]
    is_seeded    INTEGER NOT NULL DEFAULT 0      -- 1 = demo seed data; pruned when the real L4 builder runs
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Chat conversations — the full back-and-forth (user + Eva) for the Chat history
-- UI. Kept SEPARATE from the journal vault on purpose: entries/extractions hold
-- the user's own words for L1 extraction and memory recall, and Eva's replies
-- never belong there. These tables store the raw transcript so the Chat screen
-- can list past conversations and reopen them. Derived/rebuildable like the rest
-- of eva.db; deleting them loses chat history but nothing the journal needs.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,            -- UUID v4
    started_at  TEXT NOT NULL,               -- ISO-8601
    last_at     TEXT NOT NULL,               -- ISO-8601; bumped on every turn
    title       TEXT                         -- preview of the first user message
);

CREATE TABLE IF NOT EXISTS chat_turns (
    id              TEXT PRIMARY KEY,         -- UUID v4
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK(role IN ('user','eva')),
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL             -- ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_conv ON chat_turns(conversation_id, created_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- Rollup digests (week → month → era)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS digests (
    id           TEXT PRIMARY KEY,
    level        TEXT NOT NULL CHECK(level IN ('week','month','era')),
    period_start TEXT NOT NULL,            -- ISO date
    period_end   TEXT NOT NULL,
    summary      TEXT,                     -- model-narrated prose digest
    stats        TEXT,                     -- JSON: {entry_count, avg_mood, top_themes, ...}
    created_at   TEXT NOT NULL
);

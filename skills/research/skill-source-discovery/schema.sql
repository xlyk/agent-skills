-- skill-source-discovery — SQLite schema (baseline migration: 0001_init)
--
-- The database enforces the hard invariants so the agent's SQL cannot break them:
--   * UNIQUE(canonical_key) on sources       -> no duplicate sources (use ON CONFLICT DO UPDATE)
--   * UNIQUE(normalized_alias)               -> aliases collapse to one source
--   * UNIQUE(dedupe_key) on notifications    -> exactly one event per transition (ON CONFLICT DO NOTHING)
--   * delivered_at IS NULL                   -> retry-safe outbox
--
-- Apply with:  sqlite3 "$DB" < schema.sql
-- foreign_keys is per-connection: every connection must also run `PRAGMA foreign_keys = ON;`
-- (the SQL cookbook prefixes its snippets with it).

PRAGMA journal_mode = WAL;       -- persistent per-database; survives reconnects
PRAGMA foreign_keys = ON;        -- per-connection; re-asserted by every cookbook snippet

-- ---------------------------------------------------------------------------
-- migration tracking
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- sources — canonical inventory, one row per real source
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id               INTEGER PRIMARY KEY,
    canonical_key    TEXT NOT NULL UNIQUE,            -- from normalize.py; the dedup primitive
    canonical_url    TEXT NOT NULL,
    platform         TEXT NOT NULL,                   -- github|gitlab|bitbucket|forgejo|sourcehut|git|website
    host             TEXT,
    owner            TEXT,                            -- owner / group / workspace
    repo_name        TEXT,                            -- repository or path name
    source_type      TEXT NOT NULL DEFAULT 'unknown_candidate',
                     -- official_repo | community_collection_repo | personal_skill_repo
                     -- | directory_website | meta_repo | marketplace_or_catalog
                     -- | mirror | fork | article_or_blog_index | unknown_candidate
    status           TEXT NOT NULL DEFAULT 'candidate',
                     -- accepted | candidate | needs_review | rejected | dead
    confidence       REAL NOT NULL DEFAULT 0.0,
    title            TEXT,
    description      TEXT,
    license          TEXT,
    homepage_url     TEXT,
    latest_commit_sha TEXT,
    content_hash     TEXT,
    first_seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at     TEXT,
    last_checked_at  TEXT,
    last_changed_at  TEXT,
    last_notified_at TEXT,
    discovered_by    TEXT,                            -- parent source canonical_key or query id
    discovery_method TEXT,                            -- e.g. github_code_search | link_graph | seed | web_search
    fail_count       INTEGER NOT NULL DEFAULT 0,      -- consecutive unreachable scans -> dead threshold
    metadata_json    TEXT,                            -- platform-specific extras (JSON)
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (status IN ('accepted','candidate','needs_review','rejected','dead')),
    CHECK (source_type IN ('official_repo','community_collection_repo','personal_skill_repo',
                           'directory_website','meta_repo','marketplace_or_catalog','mirror',
                           'fork','article_or_blog_index','unknown_candidate'))
);
CREATE INDEX IF NOT EXISTS idx_sources_status          ON sources (status);
CREATE INDEX IF NOT EXISTS idx_sources_platform        ON sources (platform);
CREATE INDEX IF NOT EXISTS idx_sources_source_type     ON sources (source_type);
CREATE INDEX IF NOT EXISTS idx_sources_last_checked_at ON sources (last_checked_at);
CREATE INDEX IF NOT EXISTS idx_sources_last_seen_at    ON sources (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_sources_confidence      ON sources (confidence);

-- ---------------------------------------------------------------------------
-- source_aliases — alternate URLs and remote forms for one source
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_aliases (
    id               INTEGER PRIMARY KEY,
    source_id        INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    normalized_alias TEXT NOT NULL UNIQUE,            -- normalize.py alias form
    alias_url        TEXT,                            -- raw form as discovered
    kind             TEXT,                            -- https | ssh | git | redirect | canonical | website
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_source_aliases_source_id ON source_aliases (source_id);

-- ---------------------------------------------------------------------------
-- source_relationships — typed graph edges between sources (provenance)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_relationships (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    target_url  TEXT NOT NULL,                        -- canonical_url of the target (stable key)
    target_id   INTEGER REFERENCES sources (id) ON DELETE SET NULL,  -- resolved when target is ingested
    type        TEXT NOT NULL,
                -- links_to | indexes | mirrors | forks | references | homepage_for | package_for | same_as
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source_id, type, target_url),
    CHECK (type IN ('links_to','indexes','mirrors','forks','references',
                    'homepage_for','package_for','same_as'))
);
CREATE INDEX IF NOT EXISTS idx_source_rel_source_id ON source_relationships (source_id);
CREATE INDEX IF NOT EXISTS idx_source_rel_target_id ON source_relationships (target_id);

-- ---------------------------------------------------------------------------
-- source_observations — evidence + score inputs, one row per evidence item
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_observations (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    scan_id       INTEGER REFERENCES scans (id) ON DELETE SET NULL,
    evidence_type TEXT NOT NULL,                      -- e.g. has_skill_md, topic_match, no_public_access
    weight        REAL NOT NULL DEFAULT 0.0,          -- signed; from scoring-policy. positive or negative
    detail        TEXT,                               -- count, path, or short note
    detail_json   TEXT,
    observed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source_id, evidence_type)                 -- one row per signal per source -> idempotent re-scan
);
CREATE INDEX IF NOT EXISTS idx_obs_source_id ON source_observations (source_id);
CREATE INDEX IF NOT EXISTS idx_obs_scan_id   ON source_observations (scan_id);

-- ---------------------------------------------------------------------------
-- skill_candidates — skills detected inside a source (secondary, for validation/scoring)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_candidates (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    normalized_path TEXT NOT NULL,                    -- e.g. skills/foo/SKILL.md
    skill_name      TEXT,
    skill_md_hash   TEXT,                             -- content hash of SKILL.md metadata (cross-source dedupe)
    detected_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    metadata_json   TEXT,
    UNIQUE (source_id, normalized_path)
);
CREATE INDEX IF NOT EXISTS idx_skill_cand_source_id ON skill_candidates (source_id);
CREATE INDEX IF NOT EXISTS idx_skill_cand_md_hash   ON skill_candidates (skill_md_hash);

-- ---------------------------------------------------------------------------
-- scans — per-run and per-provider/source scan records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT NOT NULL,                        -- groups one discover/scan cycle
    command     TEXT NOT NULL,                        -- discover | scan | ingest
    provider    TEXT,                                 -- github | website | ... | NULL for whole-run
    source_id   INTEGER REFERENCES sources (id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'ok',           -- ok | error
    error       TEXT,
    stats_json  TEXT,                                 -- counts: scanned, candidates, accepted, dupes, events
    started_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_run_id     ON scans (run_id);
CREATE INDEX IF NOT EXISTS idx_scans_provider   ON scans (provider);
CREATE INDEX IF NOT EXISTS idx_scans_source_id  ON scans (source_id);
CREATE INDEX IF NOT EXISTS idx_scans_started_at ON scans (started_at);

-- ---------------------------------------------------------------------------
-- search_queries — configured discovery queries and their cursors (mirrored from config)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_queries (
    id          INTEGER PRIMARY KEY,
    platform    TEXT NOT NULL,
    purpose     TEXT,                                 -- e.g. skill_md | topics | awesome_lists
    template    TEXT NOT NULL,                        -- the query string / template
    enabled     INTEGER NOT NULL DEFAULT 1,
    cadence     TEXT,                                 -- daily | weekly | every_2_3_days
    cursor      TEXT,                                 -- pagination / incremental cursor
    last_run_at TEXT,
    yield_count INTEGER NOT NULL DEFAULT 0,           -- sources discovered via this query (§15)
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (platform, template)
);
CREATE INDEX IF NOT EXISTS idx_queries_enabled     ON search_queries (enabled);
CREATE INDEX IF NOT EXISTS idx_queries_last_run_at ON search_queries (last_run_at);

-- ---------------------------------------------------------------------------
-- notifications — emitted event state (the outbox); Hermes delivers, this never duplicates
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY,
    dedupe_key   TEXT NOT NULL UNIQUE,                -- e.g. accepted:<source_id> | status:<id>:<from>-><to>
    event_type   TEXT NOT NULL,
                 -- new_source.accepted | new_source.needs_review | source_status.changed
                 -- | scan.error_threshold | daily_digest.available
    source_id    INTEGER REFERENCES sources (id) ON DELETE SET NULL,
    payload_json TEXT NOT NULL,                       -- full event payload (§11.3)
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    delivered_at TEXT,                                -- NULL = pending; set by ack
    CHECK (event_type IN ('new_source.accepted','new_source.needs_review',
                          'source_status.changed','scan.error_threshold','daily_digest.available'))
);
CREATE INDEX IF NOT EXISTS idx_notif_delivered_at ON notifications (delivered_at);
CREATE INDEX IF NOT EXISTS idx_notif_event_type   ON notifications (event_type);
CREATE INDEX IF NOT EXISTS idx_notif_source_id    ON notifications (source_id);

-- ---------------------------------------------------------------------------
-- manual_overrides — reviewer decisions that survive automated rescoring
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manual_overrides (
    id         INTEGER PRIMARY KEY,
    source_id  INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    field      TEXT NOT NULL,                         -- status | source_type | confidence
                                                      -- | suppress_notifications | duplicate_of
    value      TEXT,
    reason     TEXT,
    reviewer   TEXT,
    active     INTEGER NOT NULL DEFAULT 1,            -- 0 = cleared
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source_id, field)
);
CREATE INDEX IF NOT EXISTS idx_overrides_source_id ON manual_overrides (source_id);

-- ---------------------------------------------------------------------------
-- convenience views (read-only) for reports / review / outbox
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_pending_events AS
    SELECT id, dedupe_key, event_type, source_id, payload_json, created_at
    FROM notifications
    WHERE delivered_at IS NULL
    ORDER BY created_at;

CREATE VIEW IF NOT EXISTS v_review_queue AS
    SELECT id, canonical_url, platform, source_type, status, confidence, title, last_checked_at
    FROM sources
    WHERE status IN ('needs_review', 'candidate')
    ORDER BY confidence DESC, last_seen_at DESC;

CREATE VIEW IF NOT EXISTS v_inventory_summary AS
    SELECT status, platform, source_type, COUNT(*) AS n, ROUND(AVG(confidence), 3) AS avg_confidence
    FROM sources
    GROUP BY status, platform, source_type;

CREATE VIEW IF NOT EXISTS v_dedupe_groups AS
    SELECT s.id AS source_id, s.canonical_url, COUNT(a.id) AS alias_count,
           GROUP_CONCAT(a.normalized_alias, ' | ') AS aliases
    FROM sources s
    LEFT JOIN source_aliases a ON a.source_id = s.id
    GROUP BY s.id
    HAVING alias_count > 0;

-- ---------------------------------------------------------------------------
-- record this baseline migration (idempotent)
-- ---------------------------------------------------------------------------
INSERT INTO schema_migrations (version, name)
VALUES ('0001', 'init')
ON CONFLICT (version) DO NOTHING;

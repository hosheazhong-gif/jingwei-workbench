-- Explicit 0.3 -> 0.4 migration. Adds CandidateSource without rewriting Sources or Claims.

CREATE TABLE IF NOT EXISTS candidate_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('captured', 'opened', 'promoted', 'discarded')
    ),
    opened_at TEXT,
    promoted_source_id TEXT REFERENCES sources(id),
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_sources_project
    ON candidate_sources(project_id);

UPDATE projects SET schema_version = '0.4', updated_at = datetime('now');

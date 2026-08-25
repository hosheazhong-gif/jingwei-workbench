-- Explicit 0.4 -> 0.5 migration. Adds ModelSuggestion without rewriting Sources, Claims, or draft text.

CREATE TABLE IF NOT EXISTS model_suggestions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    deliverable_block_id TEXT NOT NULL REFERENCES deliverable_blocks(id),
    kind TEXT NOT NULL CHECK (kind IN ('finding', 'option')),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'adopted', 'dismissed')),
    adopted_finding_id TEXT REFERENCES findings(id),
    adopted_option_id TEXT REFERENCES options(id),
    adapter_key TEXT NOT NULL,
    limitation TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_suggestions_block
    ON model_suggestions(deliverable_block_id);

UPDATE projects SET schema_version = '0.5', updated_at = datetime('now');

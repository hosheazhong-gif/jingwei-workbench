-- Explicit 0.2 -> 0.3 migration. Adds paragraph revisions without rewriting current_text.

CREATE TABLE IF NOT EXISTS deliverable_block_revisions (
    id TEXT PRIMARY KEY,
    deliverable_block_id TEXT NOT NULL REFERENCES deliverable_blocks(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    body TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (
        origin IN ('snapshot', 'review_candidate', 'override_candidate')
    ),
    adopted INTEGER NOT NULL DEFAULT 0 CHECK (adopted IN (0, 1)),
    review_decision_id TEXT REFERENCES review_decisions(id),
    override_decision_id TEXT REFERENCES override_decisions(id),
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (deliverable_block_id, version)
);

INSERT INTO deliverable_block_revisions (
    id, deliverable_block_id, version, body, origin, adopted,
    review_decision_id, override_decision_id, created_at, schema_version
)
SELECT
    id || '-v' || current_version,
    id,
    current_version,
    current_text,
    'snapshot',
    1,
    NULL,
    NULL,
    datetime('now'),
    '0.3'
FROM deliverable_blocks
WHERE NOT EXISTS (
    SELECT 1 FROM deliverable_block_revisions AS revision
    WHERE revision.deliverable_block_id = deliverable_blocks.id
      AND revision.version = deliverable_blocks.current_version
);

CREATE INDEX IF NOT EXISTS idx_revisions_block
    ON deliverable_block_revisions(deliverable_block_id, version);

UPDATE projects SET schema_version = '0.3', updated_at = datetime('now');

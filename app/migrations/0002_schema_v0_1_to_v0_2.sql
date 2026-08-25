-- Explicit 0.1 -> 0.2 migration. Do not rewrite 0001; apply once via schema_migrations.

PRAGMA foreign_keys = OFF;

ALTER TABLE briefs ADD COLUMN schema_version TEXT NOT NULL DEFAULT '0.2';
ALTER TABLE briefs ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE briefs ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

ALTER TABLE research_questions ADD COLUMN schema_version TEXT NOT NULL DEFAULT '0.2';
ALTER TABLE research_questions ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE research_questions ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

ALTER TABLE sources ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE sources ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE sources ADD COLUMN institution TEXT;
ALTER TABLE sources ADD COLUMN published_at TEXT;
ALTER TABLE sources ADD COLUMN original_url TEXT;
ALTER TABLE sources ADD COLUMN original_path TEXT;
ALTER TABLE sources ADD COLUMN permission TEXT;
ALTER TABLE sources ADD COLUMN sensitivity TEXT;
ALTER TABLE sources ADD COLUMN source_quality TEXT;

ALTER TABLE evidence_excerpts ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE evidence_excerpts ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

ALTER TABLE findings ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE findings ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

ALTER TABLE deliverable_blocks ADD COLUMN created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE deliverable_blocks ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

ALTER TABLE review_decisions ADD COLUMN schema_version TEXT NOT NULL DEFAULT '0.2';
ALTER TABLE review_decisions ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

ALTER TABLE override_decisions ADD COLUMN schema_version TEXT NOT NULL DEFAULT '0.2';
ALTER TABLE override_decisions ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';

CREATE TABLE claims_v02 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_id TEXT REFERENCES sources(id),
    text TEXT NOT NULL,
    epistemic_type TEXT NOT NULL CHECK (
        epistemic_type IN ('factual_claim', 'inference', 'assumption', 'judgment')
    ),
    verification_status TEXT NOT NULL CHECK (
        verification_status IN (
            'captured', 'source_checked', 'corroborated', 'conflicted',
            'stale', 'unverifiable', 'excluded'
        )
    ),
    provenance_scope TEXT,
    independently_verified INTEGER CHECK (independently_verified IN (0, 1)),
    delivery_rule TEXT,
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO claims_v02 (
    id, project_id, source_id, text, epistemic_type, verification_status,
    provenance_scope, independently_verified, delivery_rule, schema_version,
    created_at, updated_at
)
SELECT
    id,
    project_id,
    source_id,
    text,
    CASE epistemic_type WHEN 'hypothesis' THEN 'assumption' ELSE epistemic_type END,
    verification_status,
    provenance_scope,
    independently_verified,
    delivery_rule,
    '0.2',
    datetime('now'),
    datetime('now')
FROM claims;

DROP TABLE claims;
ALTER TABLE claims_v02 RENAME TO claims;

CREATE TABLE options_v02 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('candidate', 'needs_evidence', 'retained', 'deferred', 'excluded')
    ),
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO options_v02 (
    id, project_id, text, status, schema_version, created_at, updated_at
)
SELECT
    id,
    project_id,
    text,
    CASE status WHEN 'hypothesis' THEN 'candidate' ELSE status END,
    '0.2',
    datetime('now'),
    datetime('now')
FROM options;

DROP TABLE options;
ALTER TABLE options_v02 RENAME TO options;

CREATE TABLE IF NOT EXISTS deliverable_block_findings (
    deliverable_block_id TEXT NOT NULL REFERENCES deliverable_blocks(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    PRIMARY KEY (deliverable_block_id, finding_id)
);

CREATE TABLE IF NOT EXISTS deliverable_block_options (
    deliverable_block_id TEXT NOT NULL REFERENCES deliverable_blocks(id) ON DELETE CASCADE,
    option_id TEXT NOT NULL REFERENCES options(id),
    PRIMARY KEY (deliverable_block_id, option_id)
);

CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_id);
CREATE INDEX IF NOT EXISTS idx_block_claims_claim ON deliverable_block_claims(claim_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence_excerpts(source_id);
CREATE INDEX IF NOT EXISTS idx_block_findings_finding ON deliverable_block_findings(finding_id);
CREATE INDEX IF NOT EXISTS idx_block_options_option ON deliverable_block_options(option_id);

UPDATE projects SET schema_version = '0.2', updated_at = datetime('now');
UPDATE briefs SET created_at = datetime('now'), updated_at = datetime('now')
    WHERE created_at = '1970-01-01T00:00:00Z';
UPDATE research_questions SET created_at = datetime('now'), updated_at = datetime('now')
    WHERE created_at = '1970-01-01T00:00:00Z';
UPDATE sources SET schema_version = '0.2', created_at = datetime('now'), updated_at = datetime('now');
UPDATE evidence_excerpts SET schema_version = '0.2', created_at = datetime('now'), updated_at = datetime('now');
UPDATE findings SET schema_version = '0.2', created_at = datetime('now'), updated_at = datetime('now');
UPDATE deliverable_blocks SET schema_version = '0.2', created_at = datetime('now'), updated_at = datetime('now');
UPDATE review_decisions SET updated_at = datetime('now')
    WHERE updated_at = '1970-01-01T00:00:00Z';
UPDATE override_decisions SET updated_at = datetime('now')
    WHERE updated_at = '1970-01-01T00:00:00Z';

PRAGMA foreign_keys = ON;

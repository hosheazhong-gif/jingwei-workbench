PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    template_key TEXT NOT NULL,
    execution_strategy_key TEXT NOT NULL,
    stage TEXT,
    decision_gate TEXT,
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (execution_strategy_key <> '')
);

CREATE TABLE briefs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
    original_context TEXT NOT NULL,
    decision_question TEXT NOT NULL,
    deliverable TEXT NOT NULL,
    not_a_final_client_recommendation INTEGER NOT NULL DEFAULT 0 CHECK (not_a_final_client_recommendation IN (0, 1))
);

CREATE TABLE research_questions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    question TEXT NOT NULL,
    enough_for_now TEXT,
    status TEXT NOT NULL DEFAULT 'not_started'
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    file_name TEXT,
    availability TEXT NOT NULL CHECK (availability IN ('available', 'path_expired', 'permission_denied', 'deleted')),
    snapshot_path TEXT,
    content_hash TEXT,
    supersedes_source_id TEXT REFERENCES sources(id),
    limitation TEXT,
    analysis_role TEXT,
    delivery_use TEXT,
    schema_version TEXT NOT NULL
);

CREATE TABLE source_qa_requirements (
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    requirement TEXT NOT NULL,
    PRIMARY KEY (source_id, requirement)
);

CREATE TABLE evidence_excerpts (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    locator_json TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    context_limit TEXT,
    schema_version TEXT NOT NULL
);

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_id TEXT REFERENCES sources(id),
    text TEXT NOT NULL,
    epistemic_type TEXT NOT NULL CHECK (epistemic_type IN ('factual_claim', 'inference', 'hypothesis', 'judgment')),
    verification_status TEXT NOT NULL CHECK (verification_status IN ('captured', 'source_checked', 'corroborated', 'conflicted', 'stale', 'unverifiable', 'excluded')),
    provenance_scope TEXT,
    independently_verified INTEGER CHECK (independently_verified IN (0, 1)),
    delivery_rule TEXT,
    schema_version TEXT NOT NULL
);

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    evidence_excerpt_id TEXT NOT NULL REFERENCES evidence_excerpts(id),
    relation TEXT NOT NULL DEFAULT 'supports' CHECK (relation IN ('supports', 'contradicts')),
    PRIMARY KEY (claim_id, evidence_excerpt_id)
);

CREATE TABLE findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    text TEXT NOT NULL,
    confidence TEXT,
    schema_version TEXT NOT NULL
);

CREATE TABLE finding_claims (
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    relation TEXT NOT NULL DEFAULT 'supports' CHECK (relation IN ('supports', 'contradicts')),
    PRIMARY KEY (finding_id, claim_id)
);

CREATE TABLE finding_sources (
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id),
    PRIMARY KEY (finding_id, source_id)
);

CREATE TABLE finding_alternatives (
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (finding_id, position)
);

CREATE TABLE options (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

CREATE TABLE deliverable_blocks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    current_text TEXT NOT NULL DEFAULT '',
    restriction TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'draft',
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    schema_version TEXT NOT NULL
);

CREATE TABLE deliverable_block_claims (
    deliverable_block_id TEXT NOT NULL REFERENCES deliverable_blocks(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    PRIMARY KEY (deliverable_block_id, claim_id)
);

CREATE TABLE review_decisions (
    id TEXT PRIMARY KEY,
    deliverable_block_id TEXT NOT NULL REFERENCES deliverable_blocks(id),
    action TEXT NOT NULL CHECK (action IN ('approve', 'modify', 'exclude')),
    reason TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    target_version INTEGER NOT NULL
);

CREATE TABLE override_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    deliverable_block_id TEXT REFERENCES deliverable_blocks(id),
    handling TEXT NOT NULL CHECK (handling IN ('assumption', 'exclude', 'scenario')),
    reason TEXT NOT NULL,
    review_trigger TEXT,
    target_version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_claims_source ON claims(source_id);
CREATE INDEX idx_block_claims_claim ON deliverable_block_claims(claim_id);
CREATE INDEX idx_evidence_source ON evidence_excerpts(source_id);


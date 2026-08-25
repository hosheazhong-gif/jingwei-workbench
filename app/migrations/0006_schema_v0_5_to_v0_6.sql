-- Explicit 0.5 -> 0.6 migration. Adds round archive and material-to-question
-- links without rewriting Sources, Claims, hashes, or draft text.

ALTER TABLE projects ADD COLUMN current_round INTEGER NOT NULL DEFAULT 1;

ALTER TABLE research_questions ADD COLUMN round_index INTEGER NOT NULL DEFAULT 1;

ALTER TABLE candidate_sources ADD COLUMN research_question_id TEXT
    REFERENCES research_questions(id);

ALTER TABLE sources ADD COLUMN research_question_id TEXT
    REFERENCES research_questions(id);

CREATE INDEX IF NOT EXISTS idx_research_questions_round
    ON research_questions(project_id, round_index);

UPDATE projects SET schema_version = '0.6', updated_at = datetime('now');

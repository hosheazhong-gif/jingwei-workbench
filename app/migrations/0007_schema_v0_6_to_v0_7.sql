-- Explicit 0.6 -> 0.7 migration. Gives每条本轮问题一个短名，并给每一版稿记下
-- 是第几轮收下的。不复制段落、不改写正文、不动来源哈希与核验状态。
--
-- label 留空：不替既有问题编造短名，界面按整句截断显示，直到人或模型给出短名。
-- round_index 默认 1：迁移前的项目本来就只有第 1 轮。

ALTER TABLE research_questions ADD COLUMN label TEXT;

ALTER TABLE deliverable_block_revisions ADD COLUMN round_index INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_block_revisions_round
    ON deliverable_block_revisions(deliverable_block_id, round_index);

UPDATE projects SET schema_version = '0.7', updated_at = datetime('now');

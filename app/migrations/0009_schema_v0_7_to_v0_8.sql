-- Explicit 0.7 -> 0.8 migration. 给每条本轮问题记下「这条问题的答案落在稿的哪一节」。
--
-- 为什么要存下来：行业通行做法是先立骨架再找料（麦肯锡的 ghost deck、尽调第一周
-- 就把投资逻辑拆成几条可检验命题并各自定位）。经纬原来是拆完问题直接搜，答案落在
-- 哪一节要到挂原话时才临时决定，于是同一条问题的材料可能散到几节去。
--
-- target_block_id 留空是合法状态：已经存在的问题不替它猜落在哪一节，人点开时再定。
-- 只加一列，不删任何东西，不改稿、不改主张核验状态、不动来源哈希。

ALTER TABLE research_questions ADD COLUMN target_block_id TEXT
    REFERENCES deliverable_blocks(id);

CREATE INDEX IF NOT EXISTS idx_questions_target_block
    ON research_questions(target_block_id);

UPDATE projects SET schema_version = '0.8', updated_at = datetime('now');

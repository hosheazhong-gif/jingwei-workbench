-- 数据迁移，不改 schema：把早期案例专用模板 key
-- case_specific_low_info_presales 改成通用的 industry_chain_analysis_presales。
--
-- 只动 projects.template_key 这一列。不改题目名称、不改稿、不改主张核验状态、
-- 不动来源哈希与快照。schema_version 保持 0.7，本迁移不是结构变更。

UPDATE projects
SET template_key = 'industry_chain_analysis_presales'
WHERE template_key = 'case_specific_low_info_presales';

-- 011_taggroup_rename.sql
-- Milestone A: structural rename — rounds→tag_group, meta_id→target_puzzle_id,
-- meta_code→group_code, round_id→tag_group_id in tags/rollups.
-- FK constraint names left as-is (cosmetic); they auto-repoint after RENAME TABLE.

-- 1. Rename the umbrella table.
RENAME TABLE `rounds` TO `tag_group`;

-- 2. Rename columns in tag_group (meta_id has no FK; plain CHANGE COLUMN).
ALTER TABLE `tag_group`
  CHANGE COLUMN `meta_id`   `target_puzzle_id` int(11)  DEFAULT NULL,
  CHANGE COLUMN `meta_code` `group_code`       char(6)  DEFAULT '0';

-- 3. Rename FK column in tags (RoundID FK follows; tag_uq index follows).
ALTER TABLE `tags`
  CHANGE COLUMN `round_id` `tag_group_id` int(11) NOT NULL;

-- 4. Rename FK column in rollups (RollupRoundID FK follows).
ALTER TABLE `rollups`
  CHANGE COLUMN `round_id` `tag_group_id` int(11) NULL;

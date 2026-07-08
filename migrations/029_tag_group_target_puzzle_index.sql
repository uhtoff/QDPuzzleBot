-- tag_group.target_puzzle_id has had no index since the meta_id rename in migration
-- 011 (plain CHANGE COLUMN, no FK/index). get_is_target() (added 2026-06-25) queries
-- this column on every single puzzle read via construct_puzzle, so every puzzle
-- lookup app-wide has been doing a full table scan of tag_group.
ALTER TABLE `tag_group`
  ADD KEY `idx_tag_group_target_puzzle` (`target_puzzle_id`);

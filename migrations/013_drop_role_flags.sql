-- 013_drop_role_flags.sql
-- is-target is now derived from tag_group.target_puzzle_id (eager-loaded);
-- metameta was dead (never written). Drop both denormalised role-flag columns.
ALTER TABLE `puzzles` DROP COLUMN `metapuzzle`;
ALTER TABLE `puzzles` DROP COLUMN `metameta`;

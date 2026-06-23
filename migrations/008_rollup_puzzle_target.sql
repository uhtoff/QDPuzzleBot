-- M8: puzzle-keyed roll-ups — let a minimeta (a puzzle) be a roll-up target so its
-- mini-puzzles render onto the minimeta's OWN sheet, reusing the rollup engine.
--
-- The rollups table was round-keyed only (round_id NOT NULL, one row per meta round).
-- This generalises it to "tables" keyed by EITHER a round (existing meta rounds) OR a
-- target puzzle (minimetas). Additive + backward compatible: existing rows keep their
-- round_id and a NULL target_puzzle_id; the generic commit() writes the new field by
-- name. MySQL UNIQUE indexes permit multiple NULLs, so the two uniques coexist (a
-- round-keyed row has target_puzzle_id NULL; a puzzle-keyed row has round_id NULL).
--
-- Down: drop target_puzzle_id + its FK/unique, and restore round_id NOT NULL (only
-- valid while no puzzle-keyed rows exist).

ALTER TABLE `rollups`
  ADD COLUMN `target_puzzle_id` bigint DEFAULT NULL AFTER `round_id`,
  MODIFY COLUMN `round_id` int(11) NULL,
  ADD UNIQUE KEY `uq_rollups_target_puzzle` (`target_puzzle_id`),
  ADD CONSTRAINT `RollupTargetPuzzleID` FOREIGN KEY (`target_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

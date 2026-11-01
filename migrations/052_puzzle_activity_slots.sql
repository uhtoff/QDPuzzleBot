-- 052_puzzle_activity_slots.sql
-- Per-puzzle activity in 5-minute slots, for the dashboard sparkline
-- (docs/superpowers/specs/2026-11-01-puzzle-activity-sparkline-design.md).
--   chat_count    human Discord messages in the puzzle's channel during the slot
--   sheet_edited  1 if the puzzle's own spreadsheet (files mode) was edited by
--                 someone other than the bot during the slot
-- Only slots with activity get a row. Rows go with their puzzle (cascade).
--
-- PRE-FLIGHT: none needed. A new table; nothing reads it before the code that
-- writes it ships. Re-runnable: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS `puzzle_activity_slots` (
  `puzzle_id`    INT(11)      NOT NULL,
  `slot_start`   DATETIME     NOT NULL,
  `chat_count`   INT UNSIGNED NOT NULL DEFAULT 0,
  `sheet_edited` TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (`puzzle_id`, `slot_start`),
  CONSTRAINT `PuzzleActivityPuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 024: Rename the "archive sheet" (a second spreadsheet holding solved feeders) to the
-- "solved puzzles sheet", and drop the never-wired auto-archive feature's archive_time
-- columns. See docs/superpowers/specs/2026-07-02-solved-sheet-rename-design.md.

-- Part A — renames (CHANGE COLUMN restates the type; confirmed via live schema)
ALTER TABLE `hunts`
  CHANGE COLUMN `archive_google_sheet_id` `solved_sheet_id` varchar(50) DEFAULT NULL;

ALTER TABLE `puzzles`
  CHANGE COLUMN `archived` `on_solved_sheet` tinyint(1) DEFAULT 0;

-- Part B — drop the dead archive_time trio (never read in any live path)
ALTER TABLE `hunts`     DROP COLUMN `archive_time`;
ALTER TABLE `tag_group` DROP COLUMN `archive_time`;
ALTER TABLE `puzzles`   DROP COLUMN `archive_time`;

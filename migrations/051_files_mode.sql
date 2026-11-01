-- 051_files_mode.sql
-- Per-hunt "files" sheet mode: one Google spreadsheet per puzzle.
--   hunts.sheet_mode        'tabs' (today's behaviour, the default) or 'files'
--   hunts.drive_folder_id   the hunt's Drive folder (files mode), '' otherwise
--   hunts.solved_folder_id  its `Solved` subfolder (files mode), '' otherwise
--   puzzles.google_sheet_id / additional_sheets.google_sheet_id
--                           the object's OWN spreadsheet; '' means "a tab on
--                           the hunt's live (or solved) spreadsheet", as today.
--
-- PRE-FLIGHT: none needed. All five columns are additive and NOT NULL with a
-- default that reproduces the behaviour of the code that predates them
-- ('tabs' / ''), so every existing hunt, puzzle and extra sheet stays a tab
-- hunt with no manual step. Checked: no existing column of these names, and
-- no code reads them before this migration (the dataclasses gain them in the
-- same commit). Safe to apply before the code ships.
--
-- Re-runnable: ADD COLUMN IF NOT EXISTS (MariaDB), so a second apply is a no-op.
ALTER TABLE `hunts`
  ADD COLUMN IF NOT EXISTS `sheet_mode` VARCHAR(10) NOT NULL DEFAULT 'tabs',
  ADD COLUMN IF NOT EXISTS `drive_folder_id` VARCHAR(100) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS `solved_folder_id` VARCHAR(100) NOT NULL DEFAULT '';
ALTER TABLE `puzzles`
  ADD COLUMN IF NOT EXISTS `google_sheet_id` VARCHAR(100) NOT NULL DEFAULT '';
ALTER TABLE `additional_sheets`
  ADD COLUMN IF NOT EXISTS `google_sheet_id` VARCHAR(100) NOT NULL DEFAULT '';

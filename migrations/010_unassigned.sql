-- Meta-matching: store the page/table ids for the per-hunt Unassigned Puzzles tab.
ALTER TABLE `hunts`
  ADD COLUMN `unassigned_sheet_page_id` INT(11) DEFAULT NULL,
  ADD COLUMN `unassigned_table_id`       INT(11) DEFAULT NULL;

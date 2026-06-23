-- Meta-matching: store the page/table ids for the per-hunt Unclassified Puzzles tab.
ALTER TABLE `hunts`
  ADD COLUMN `unclassified_sheet_page_id` INT(11) DEFAULT NULL,
  ADD COLUMN `unclassified_table_id`       INT(11) DEFAULT NULL;

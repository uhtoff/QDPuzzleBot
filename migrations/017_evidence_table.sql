-- Evidence board: store the native-Table id for the per-hunt Evidence tab, so the
-- tab is rendered as a real Google Sheets Table (like Unassigned Feeders) instead
-- of a flat aggregate_to_tab value list. NULL until the Table is created lazily.
ALTER TABLE `hunts`
  ADD COLUMN `evidence_table_id` INT(11) DEFAULT NULL;

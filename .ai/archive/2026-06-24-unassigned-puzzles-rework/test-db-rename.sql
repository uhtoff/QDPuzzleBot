-- ONE-OFF for the test DB (puzzlebot_test) ONLY.
-- Migration 010 already ran there under the OLD column names, then the branch
-- renamed them. Run this once to bring the existing DB in line with the renamed
-- migration. Fresh DBs that apply 010_unassigned.sql do NOT need this.
--
-- MySQL 8.0+ / MariaDB 10.5.2+:
ALTER TABLE `hunts`
  RENAME COLUMN `unclassified_sheet_page_id` TO `unassigned_sheet_page_id`,
  RENAME COLUMN `unclassified_table_id`      TO `unassigned_table_id`;

-- Older servers without RENAME COLUMN — use instead:
-- ALTER TABLE `hunts`
--   CHANGE COLUMN `unclassified_sheet_page_id` `unassigned_sheet_page_id` INT(11) DEFAULT NULL,
--   CHANGE COLUMN `unclassified_table_id`      `unassigned_table_id`      INT(11) DEFAULT NULL;

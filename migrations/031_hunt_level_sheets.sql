-- 031_hunt_level_sheets.sql
-- Plan C (hunt-level working sheets). Two changes:
--
-- 1. additional_sheets gains a nullable hunt_id. puzzle_id is ALREADY nullable
--    (022's cleanup guards `WHERE a.puzzle_id IS NOT NULL`), but with no hunt_id
--    a puzzle-less row was unreachable -- every read path went through a puzzle.
--    A hunt-level sheet is exactly `puzzle_id IS NULL AND hunt_id = <h>`.
--
-- 2. evidence_items gains source_sheet_id: a fact can come from a hunt-level
--    working sheet rather than from a puzzle. At most one of source_puzzle_id /
--    source_sheet_id is set; that invariant is owned by the service methods that
--    write each kind, not by a check constraint (this schema uses none).
--
-- Deleting a sheet must not delete the notes written about it, so
-- source_sheet_id is ON DELETE SET NULL -- the same choice made for
-- evidence_items.media_id in 030. additional_sheets.hunt_id is ON DELETE CASCADE:
-- a hunt's sheets are part of the hunt and go with it.

ALTER TABLE `additional_sheets`
  ADD COLUMN `hunt_id` INT(11) DEFAULT NULL,
  ADD KEY `idx_additional_sheets_hunt` (`hunt_id`),
  ADD CONSTRAINT `AdditionalSheetHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE `evidence_items`
  ADD COLUMN `source_sheet_id` INT(11) DEFAULT NULL,
  ADD CONSTRAINT `EvidenceSourceSheetID` FOREIGN KEY (`source_sheet_id`)
      REFERENCES `additional_sheets` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;

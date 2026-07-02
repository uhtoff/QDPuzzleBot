-- 022_delete_cascades.sql
-- Findings #32/#42/#55 (docs/reviews/2026-07-02-staging-full-review.md): evidence_items,
-- additional_sheets, and tag_group_category all lack FKs to their owning row, so no
-- delete path (delete_puzzle, delete_round, delete_hunt, convert_to_puzzle) ever cleaned
-- them up -- rows orphan forever, confirmed on this DB (14/20 evidence_items had a
-- dangling source_puzzle_id, 101/118 additional_sheets a dangling puzzle_id). Add FKs so
-- a puzzle/hunt/tag_group delete cascades automatically, regardless of which code path
-- performed it, closing the gap for every current and future delete site at once.
--
-- Existing orphan rows would violate a NOT NULL FK, so purge/null them first -- they're
-- already unreachable dead data (their owning puzzle/hunt is already gone).

DELETE e FROM evidence_items e
  LEFT JOIN puzzles p ON e.source_puzzle_id = p.id
  WHERE p.id IS NULL;

DELETE e FROM evidence_items e
  LEFT JOIN hunts h ON e.hunt_id = h.id
  WHERE h.id IS NULL;

UPDATE evidence_items e
  LEFT JOIN puzzles p ON e.target_puzzle_id = p.id
  SET e.target_puzzle_id = NULL
  WHERE e.target_puzzle_id IS NOT NULL AND p.id IS NULL;

DELETE a FROM additional_sheets a
  LEFT JOIN puzzles p ON a.puzzle_id = p.id
  WHERE a.puzzle_id IS NOT NULL AND p.id IS NULL;

ALTER TABLE `evidence_items`
  ADD CONSTRAINT `EvidenceSourcePuzzleID` FOREIGN KEY (`source_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT `EvidenceHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT `EvidenceTargetPuzzleID` FOREIGN KEY (`target_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE `additional_sheets`
  ADD CONSTRAINT `AdditionalSheetPuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- group_id was declared bigint(20) unsigned (copy-pasted from category_id, which
-- legitimately holds a Discord snowflake) but it's actually an internal tag_group.id
-- (int(11)) -- a FK requires identical column types (errno 150 otherwise). Table is
-- currently empty on this DB, so the narrowing MODIFY is data-safe; category_id is left
-- alone since it genuinely holds a Discord snowflake with no local table to FK against.
ALTER TABLE `tag_group_category`
  MODIFY COLUMN `group_id` int(11) NOT NULL;

ALTER TABLE `tag_group_category`
  ADD CONSTRAINT `TagGroupCategoryGroupID` FOREIGN KEY (`group_id`)
      REFERENCES `tag_group` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

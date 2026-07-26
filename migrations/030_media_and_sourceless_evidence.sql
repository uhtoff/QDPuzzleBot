-- 030_media_and_sourceless_evidence.sql
-- Plan A (image evidence). Two changes:
--
-- 1. A `media` table: one row per stored image, bytes on disk under
--    config.web_archive_media_dir (the same root the Discord message bridge
--    writes to). No pixel dimensions are stored -- the browser knows the
--    natural size and coordinates are normalised, so nothing server-side needs
--    them, which is what keeps Pillow out of the dependency list.
--
-- 2. evidence_items can now carry an image (media_id) and can have NO source
--    puzzle at all (source_puzzle_id nullable). A fact can belong to the hunt
--    rather than to any one puzzle.
--
-- source_puzzle_id carries the EvidenceSourcePuzzleID FK from 022 with
-- ON DELETE CASCADE. MySQL will not reliably MODIFY the nullability of a
-- column under an active FK, so it is dropped and re-added around the change.
-- The CASCADE semantics are deliberately UNCHANGED: deleting a puzzle still
-- deletes evidence sourced from it. (NULL rows bypass the FK check entirely,
-- which is what makes sourceless evidence legal.)

CREATE TABLE IF NOT EXISTS `media` (
  `id`           INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`      INT(11)      NOT NULL,
  `path`         VARCHAR(512) NOT NULL,
  `filename`     VARCHAR(255) NOT NULL DEFAULT '',
  `content_type` VARCHAR(100) NOT NULL DEFAULT '',
  `size`         INT(11)      NOT NULL DEFAULT 0,
  `origin`       VARCHAR(16)  NOT NULL DEFAULT 'upload',
  `created_at`   DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_media_hunt` (`hunt_id`),
  CONSTRAINT `MediaHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

ALTER TABLE `evidence_items`
  DROP FOREIGN KEY `EvidenceSourcePuzzleID`;

ALTER TABLE `evidence_items`
  MODIFY COLUMN `source_puzzle_id` INT(11) DEFAULT NULL;

ALTER TABLE `evidence_items`
  ADD CONSTRAINT `EvidenceSourcePuzzleID` FOREIGN KEY (`source_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- Deleting an image must not delete the note written about it.
ALTER TABLE `evidence_items`
  ADD COLUMN `media_id` INT(11) DEFAULT NULL,
  ADD CONSTRAINT `EvidenceMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;

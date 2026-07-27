-- 035_puzzle_images.sql
-- A puzzle carries an ordered set of images, from two sources: a locates
-- region's crop ("this section IS the puzzle"), and a direct upload.
--
-- Materialised rather than derived from canvas_regions, because the two
-- sources mix in ONE ordered list and you cannot order a list where half the
-- entries exist only as a query result.
--
-- Deliberately NOT evidence_items: a locates region is not a clue about
-- anything, and one row per region on a big sheet would swamp the evidence
-- table. The puzzle's own picture belongs to the puzzle.

CREATE TABLE IF NOT EXISTS `puzzle_images` (
  `id`               INT(11)  NOT NULL AUTO_INCREMENT,
  `puzzle_id`        INT(11)  NOT NULL,
  `media_id`         INT(11)  NOT NULL,
  -- Set when this image came from a locates region; NULL for a direct upload,
  -- which is what keeps uploads untouched by any region change.
  `source_region_id` INT(11)  DEFAULT NULL,
  `position`         INT(11)  NOT NULL DEFAULT 0,
  `created_at`       DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  -- One region yields at most one image row: this is what makes the sync
  -- idempotent. MySQL permits many NULLs in a unique index, so direct uploads
  -- are unaffected.
  UNIQUE KEY `uniq_puzzle_images_region` (`source_region_id`),
  KEY `idx_puzzle_images_puzzle` (`puzzle_id`),
  CONSTRAINT `PuzzleImagePuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `PuzzleImageMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  -- CASCADE, matching evidence_items.source_region_id (033): a section image
  -- is created BY the link and is meaningless without the region.
  CONSTRAINT `PuzzleImageRegionID` FOREIGN KEY (`source_region_id`)
      REFERENCES `canvas_regions` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- A region's crop, so it can be FOUND. Without this the only record of a crop
-- is the media_id on an informs region's evidence row, and a locates region
-- would have no route back to its own crop at all. SET NULL, not CASCADE: a
-- region outlives its crop.
ALTER TABLE `canvas_regions`
  ADD COLUMN `crop_media_id` INT(11) DEFAULT NULL,
  ADD KEY `idx_canvas_regions_crop` (`crop_media_id`),
  ADD CONSTRAINT `CanvasRegionCropMediaID` FOREIGN KEY (`crop_media_id`)
      REFERENCES `media` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- 033_canvases.sql
-- Plan B2a. A hunt can hold big images that get marked up: rectangles drawn on
-- the picture, each optionally linked to a puzzle.
--
-- Separate tables rather than more columns on puzzles: a region is not a puzzle,
-- and an informs-region spawns a real evidence_items row so that routing, the
-- evidence panel and the contribution table all work unchanged.

CREATE TABLE IF NOT EXISTS `hunt_canvases` (
  `id`         INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`    INT(11)      NOT NULL,
  `media_id`   INT(11)      NOT NULL,
  `name`       VARCHAR(255) NOT NULL DEFAULT '',
  `note`       TEXT         DEFAULT NULL,
  `created_at` DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_hunt_canvases_hunt` (`hunt_id`),
  CONSTRAINT `HuntCanvasHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  -- The image IS the canvas: without it there is nothing to mark up, so this
  -- cascades rather than SET NULLing into a canvas that cannot render.
  CONSTRAINT `HuntCanvasMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `canvas_regions` (
  `id`         INT(11)      NOT NULL AUTO_INCREMENT,
  `canvas_id`  INT(11)      NOT NULL,
  -- JSON: {"type":"rect","points":[[x1,y1],[x2,y2]]}, every value 0-1.
  -- A blob, not four columns, so other shapes become a later UI change rather
  -- than a migration. Coordinates are normalised so a region survives re-tiling
  -- and any change of image resolution.
  `shape`      TEXT         NOT NULL,
  `label`      VARCHAR(255) NOT NULL DEFAULT '',
  `note`       TEXT         DEFAULT NULL,
  `puzzle_id`  INT(11)      DEFAULT NULL,
  -- 'locates' (this part of the image IS that puzzle) or 'informs' (this part is
  -- a clue FOR that puzzle). NULL exactly when puzzle_id is NULL.
  `role`       VARCHAR(16)  DEFAULT NULL,
  `created_at` DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_canvas_regions_canvas` (`canvas_id`),
  KEY `idx_canvas_regions_puzzle` (`puzzle_id`),
  CONSTRAINT `CanvasRegionCanvasID` FOREIGN KEY (`canvas_id`)
      REFERENCES `hunt_canvases` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  -- Deleting a puzzle unlinks the region rather than destroying the annotation:
  -- the rectangle and its transcription are work someone did on the picture and
  -- outlive the puzzle record.
  CONSTRAINT `CanvasRegionPuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- A region can be an evidence item's provenance. CASCADE, not SET NULL: unlike a
-- sheet (031) or a media row (030), an informs-region's evidence row is created
-- BY the link and is meaningless without it. canvas.py owns both sides so they
-- cannot drift apart, and this FK enforces the same thing if anything else ever
-- deletes a region.
ALTER TABLE `evidence_items`
  ADD COLUMN `source_region_id` INT(11) DEFAULT NULL,
  ADD KEY `idx_evidence_source_region` (`source_region_id`),
  ADD CONSTRAINT `EvidenceSourceRegionID` FOREIGN KEY (`source_region_id`)
      REFERENCES `canvas_regions` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

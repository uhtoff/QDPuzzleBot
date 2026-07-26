-- 032_media_tiles.sql
-- Pixel dimensions and tile-pyramid state for stored images.
--
-- This reverses the original canvas design's "no pixel dimensions are stored".
-- That was justified by region coordinates being normalised 0-1 -- still true --
-- but a tile pyramid needs the source dimensions to know how many zoom levels
-- exist, and the browser cannot report the natural size of an image it is never
-- able to decode.
--
-- Existing rows default to 'none'/0, which is correct: the evidence screenshots
-- Plan A stores have no pyramid and need none.

ALTER TABLE `media`
  ADD COLUMN `width`       INT(11)     NOT NULL DEFAULT 0 AFTER `size`,
  ADD COLUMN `height`      INT(11)     NOT NULL DEFAULT 0 AFTER `width`,
  -- none: small enough to serve whole, no pyramid wanted.
  -- pending: tiling queued or running. ready: pyramid on disk. failed: gave up.
  ADD COLUMN `tile_status` VARCHAR(16) NOT NULL DEFAULT 'none' AFTER `height`,
  -- Number of pyramid levels, 0 when there is no pyramid. The deepest level
  -- index is tile_levels - 1 and is native resolution.
  ADD COLUMN `tile_levels` INT(11)     NOT NULL DEFAULT 0 AFTER `tile_status`;

-- 034_media_markup.sql
-- Purely visual annotation on a stored image: freehand strokes, lines/arrows
-- and text boxes. Keyed on media rather than on a canvas or an evidence row, so
-- one table serves every image surface, and coordinates are normalised 0-1 like
-- canvas_regions so a mark survives any change of resolution.
--
-- Markup carries NO meaning: it is never linked to a puzzle, never spawns an
-- evidence row, and is never cropped or OCR'd. That is what keeps canvas.py's
-- region/evidence coupling out of this feature entirely.

CREATE TABLE IF NOT EXISTS `media_markup` (
  `id`         INT(11)     NOT NULL AUTO_INCREMENT,
  `media_id`   INT(11)     NOT NULL,
  -- 'pen' | 'line' | 'text'. A highlighter is a pen with a fat, low-opacity
  -- style, and an arrow is a line with a style flag: neither is a kind, because
  -- nothing server-side treats them differently.
  `kind`       VARCHAR(16) NOT NULL,
  -- JSON: {"points":[[x,y],...]} for pen/line, {"points":[[x,y]],"size":0.04}
  -- for text. Written only by markup_shape.parse_mark.
  `shape`      TEXT        NOT NULL,
  -- JSON: {"colour":"#e03131","width":0.004,"opacity":1,"arrow":false}
  `style`      TEXT        NOT NULL,
  `text`       TEXT        DEFAULT NULL,
  `created_at` DATETIME    NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_media_markup_media` (`media_id`),
  -- CASCADE, not SET NULL: ink without its image is meaningless, and media
  -- already cascades from hunts, so a deleted hunt leaves no orphan marks.
  CONSTRAINT `MediaMarkupMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

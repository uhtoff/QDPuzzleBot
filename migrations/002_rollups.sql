-- M5: rollups — one tracked record per aggregation target (a meta round's sheet).
-- Additive and backward-compatible: no existing column/query changes. Run before
-- deploying the M5 code (the generic commit() writes every dataclass field by name).
--
-- Columns source_mode / edge_type_filter are written at their defaults now and stay
-- inert until M6/M7 wire them up. last_ok / last_error / updated_at carry the
-- loud-failure state surfaced as a dashboard "sheet stale" badge. tab_page_id is the
-- cached destination tab; metadata_id is the createDeveloperMetadata id that survives
-- the archive sheet-ID reassignment (with the meta puzzle's google_page_id as the
-- trusted fallback in code).

CREATE TABLE IF NOT EXISTS `rollups` (
  `id`               int(11)         NOT NULL AUTO_INCREMENT,
  `hunt_id`          int(11)         NOT NULL,
  `round_id`         int(11)         NOT NULL,
  `tab_page_id`      bigint          DEFAULT NULL,
  `metadata_id`      bigint          DEFAULT NULL,
  `source_mode`      varchar(32)     NOT NULL DEFAULT 'round',
  `edge_type_filter` varchar(32)     DEFAULT 'feeds-meta',
  `last_ok`          tinyint(1)      NOT NULL DEFAULT 1,
  `last_error`       text            DEFAULT NULL,
  `updated_at`       datetime        DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_rollups_round` (`round_id`),
  KEY `idx_rollups_hunt` (`hunt_id`),
  CONSTRAINT `RollupHuntID`  FOREIGN KEY (`hunt_id`)  REFERENCES `hunts`  (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `RollupRoundID` FOREIGN KEY (`round_id`) REFERENCES `rounds` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

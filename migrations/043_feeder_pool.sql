-- Feeder pool: the given-feeder list for a meta matching puzzle.
--
-- A Minimeta owns a pool of finished answer strings the puzzle handed the solvers.
-- Its Minis (and the Minimeta itself) CLAIM items out of the pool; a claim becomes
-- confirmed when the claiming mini is solved. See
-- docs/superpowers/specs/2026-08-12-feeder-pool-design.md.
--
-- Both tables are new, so there is no existing data to verify against and no
-- backfill: the pre-flight check other migrations carry does not apply here.
--
-- Down: DROP TABLE feeder_claims; DROP TABLE feeder_pool_items;

CREATE TABLE `feeder_pool_items` (
  `id`                 int(11) NOT NULL AUTO_INCREMENT,
  `minimeta_puzzle_id` int(11) NOT NULL,
  `text`               varchar(255) NOT NULL,
  `note`               text DEFAULT NULL,
  `position`           int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_feeder_pool_items_text` (`minimeta_puzzle_id`, `text`),
  CONSTRAINT `FeederPoolMinimetaID` FOREIGN KEY (`minimeta_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `feeder_claims` (
  `id`        int(11) NOT NULL AUTO_INCREMENT,
  `item_id`   int(11) NOT NULL,
  `target_id` int(11) NOT NULL,
  `confirmed` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_feeder_claims_item_target` (`item_id`, `target_id`),
  KEY `ix_feeder_claims_target` (`target_id`),
  CONSTRAINT `FeederClaimItemID` FOREIGN KEY (`item_id`)
      REFERENCES `feeder_pool_items` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `FeederClaimTargetID` FOREIGN KEY (`target_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

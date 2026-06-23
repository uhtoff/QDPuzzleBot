-- M9: evidence items for the hunt-wide evidence board.
CREATE TABLE IF NOT EXISTS `evidence_items` (
  `id`               INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`          INT(11)      NOT NULL,
  `source_puzzle_id` INT(11)      NOT NULL,
  `target_puzzle_id` INT(11)      DEFAULT NULL,
  `note`             TEXT         NOT NULL,
  `created_at`       DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_evidence_hunt`   (`hunt_id`),
  KEY `idx_evidence_source` (`source_puzzle_id`),
  KEY `idx_evidence_target` (`target_puzzle_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Store the page id of the Evidence sheet tab once created.
ALTER TABLE `hunts`
  ADD COLUMN `evidence_sheet_page_id` INT(11) DEFAULT NULL;

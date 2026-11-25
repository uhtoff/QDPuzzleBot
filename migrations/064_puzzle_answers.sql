-- 064_puzzle_answers.sql
-- Puzzle answers move into their own rows, with an expected-answer count.
--   puzzle_answers                       one row per answer; position orders them.
--                                        KEY (puzzle_id, position) is deliberately
--                                        not UNIQUE so a reorder can swap positions.
--   puzzles.expected_answers             how many answers the puzzle needs (0 = the
--                                        completion tick alone).
--   puzzles.expected_answers_confirmed   0 until someone confirms the count.
--
-- PRE-FLIGHT: run in EVERY environment before pushing (defaulted columns do not
-- prove themselves by applying):
--   SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
--     AND TABLE_NAME='puzzles' AND COLUMN_NAME IN ('expected_answers','expected_answers_confirmed');
--   SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()
--     AND TABLE_NAME='puzzle_answers';
-- both must return 0.
--
-- Two statements, each safe to rerun after a failure at the other. Plain ADD COLUMN
-- (not MariaDB's IF NOT EXISTS, which CI's MySQL 8 rejects).
CREATE TABLE IF NOT EXISTS `puzzle_answers` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `puzzle_id` INT NOT NULL,
  `answer` VARCHAR(250) NOT NULL,
  `position` INT NOT NULL,
  `no_meta` TINYINT(1) NOT NULL DEFAULT 0,
  `created_by` VARCHAR(255) NULL,
  `created_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `puzzle_answers_puzzle_position` (`puzzle_id`, `position`),
  CONSTRAINT `puzzle_answers_puzzle_fk` FOREIGN KEY (`puzzle_id`) REFERENCES `puzzles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

ALTER TABLE `puzzles`
  ADD COLUMN `expected_answers` INT NOT NULL DEFAULT 1,
  ADD COLUMN `expected_answers_confirmed` TINYINT(1) NOT NULL DEFAULT 1;

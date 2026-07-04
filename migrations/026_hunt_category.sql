-- 026_hunt_category.sql
-- Mirrors tag_group_category (014), but for a hunt's own general category once
-- it grows past the soft limit. Absence of rows for a hunt == legacy
-- single-category (divider) layout.
CREATE TABLE IF NOT EXISTS `hunt_category` (
  `id`          BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `hunt_id`     BIGINT(20) UNSIGNED NOT NULL,
  `category_id` BIGINT(20) UNSIGNED NOT NULL,
  `role`        VARCHAR(16) NOT NULL DEFAULT 'unsolved',  -- 'unsolved' | 'solved'
  `seq`         INT NOT NULL DEFAULT 0,                    -- 0,1,2… within a role
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hunt_category` (`hunt_id`, `category_id`),
  KEY `idx_hunt` (`hunt_id`),
  KEY `idx_category` (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

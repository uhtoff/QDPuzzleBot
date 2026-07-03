-- 014_tag_group_category.sql
-- A tag_group can span multiple Discord categories once it grows past the
-- soft limit. This table records every category a group owns and its role.
-- Absence of rows for a group == legacy single-category (divider) layout.
CREATE TABLE IF NOT EXISTS `tag_group_category` (
  `id`          BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `group_id`    BIGINT(20) UNSIGNED NOT NULL,
  `category_id` BIGINT(20) UNSIGNED NOT NULL,
  `role`        VARCHAR(16) NOT NULL DEFAULT 'unsolved',  -- 'unsolved' | 'solved'
  `seq`         INT NOT NULL DEFAULT 0,                    -- 0,1,2… within a role
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_group_category` (`group_id`, `category_id`),
  KEY `idx_group` (`group_id`),
  KEY `idx_category` (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

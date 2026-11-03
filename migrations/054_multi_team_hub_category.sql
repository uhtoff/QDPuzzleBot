-- 054_multi_team_hub_category.sql
-- The hub category a multi-team hunt's no-spoiler and finishers channels are
-- made in (chosen at setup; "general" by default, created if missing). NULL =
-- uncategorised, as every row made before this migration.
--
-- PRE-FLIGHT: none needed. A nullable column with no default value to check;
-- plain ADD COLUMN (CI runs MySQL 8.0, so no MariaDB-only IF NOT EXISTS).

ALTER TABLE `multi_team_hunts`
  ADD COLUMN `hub_category_id` BIGINT(20) UNSIGNED NULL DEFAULT NULL AFTER `name`;

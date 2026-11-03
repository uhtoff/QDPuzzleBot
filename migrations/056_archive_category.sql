-- 056_archive_category.sql
-- A server setting: the category archive channels are put at the top of (made
-- if missing). On a hub, each team's hub archive and each multi-team hunt's
-- "all teams" archive; on any server, an in-place !archive_hunt's renamed
-- channel. Empty = top level, which is how every archive was placed before.
-- Set on Server settings or with !update_settings archive_category "<name>".
--
-- PRE-FLIGHT: none needed. Additive column; NOT NULL with a default of '',
-- which keeps every existing server's behaviour.

ALTER TABLE `guilds`
  ADD COLUMN `archive_category` VARCHAR(100) NOT NULL DEFAULT '';

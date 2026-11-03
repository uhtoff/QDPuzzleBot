-- 055_hub_multi_team_category.sql
-- A hub's setting: the category its multi-team hunts' no-spoiler and finishers
-- channels are made in (created if the hub has none by that name). Empty = top
-- level. Set on the hub's Server settings page or with
-- !update_settings multi_team_category "<name>". Each multi-team hunt still
-- records the category its own channels were made in (054).
--
-- PRE-FLIGHT: none needed. Additive column; NOT NULL with a default, so every
-- existing row reads "general", which is the intended default.

ALTER TABLE `guilds`
  ADD COLUMN `multi_team_category` VARCHAR(100) NOT NULL DEFAULT 'general';

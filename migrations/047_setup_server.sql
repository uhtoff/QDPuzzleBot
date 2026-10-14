-- 047_setup_server.sql
-- Slice 2 of docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md:
-- !setup_server (plan docs/superpowers/plans/2026-10-14-setup-server.md).
--
-- guilds.setup_complete_time -- !setup_server's run-once lock. NULL means "never
-- completed"; the command stamps it as its very last step and refuses on any
-- guild where it is set. Written only by GuildSettingsDb.mark_setup_complete,
-- never by commit(), so no settings write can clear it.
--
-- hunts.roster_role_id -- the HUB role (made by hand on the hub) listing a
-- team-server hunt's players: the invite DM list for slice 3, and what the hub's
-- post-hunt spoilers channel is gated by. Typed like hunts.role_id.
--
-- PRE-FLIGHT: none needed, and here is why rather than a query. Both columns are
-- additive and defaulted, so this applies cleanly anywhere; the question is
-- only whether the default is RIGHT for existing rows. It is, by construction:
-- !setup_server does not exist before this migration, so no guild has completed
-- it (NULL is true for every row) and no hunt has a hub roster role (NULL is
-- true for every row). Nothing reads either column as meaningful except the
-- code that ships alongside.
--
-- Re-runnable only in the sense that a second apply fails loudly on the
-- duplicate column.

ALTER TABLE `guilds`
  ADD COLUMN `setup_complete_time` DATETIME NULL DEFAULT NULL;

ALTER TABLE `hunts`
  ADD COLUMN `roster_role_id` bigint(20) unsigned DEFAULT NULL AFTER `role_id`;

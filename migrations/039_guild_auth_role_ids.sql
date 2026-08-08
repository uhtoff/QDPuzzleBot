-- 039_guild_auth_role_ids.sql
-- M8 (docs/reviews/2026-08-07-comprehensive-review.md): authorisation was keyed on Discord
-- role *names* — ADMIN_ROLES = ('Moderator', 'mod', 'admin') matched by exact string against
-- member.roles. Anyone holding Discord's Manage Roles permission could create a role called
-- `mod`, self-assign it, and gain full dashboard admin; the names can also pre-exist in a
-- guild for entirely unrelated reasons.
--
-- These columns hold the per-guild role *ids* each tier resolves to: a comma-separated list
-- of Discord role snowflakes. Snowflakes are 64-bit and there is no fixed cardinality, so
-- TEXT rather than a numeric column or a fixed set of them.
--
-- NULL/empty means NOT CONFIGURED, and is the state every existing row starts in: the
-- resolver then falls back to matching by name exactly as before, so this migration alone
-- changes no behaviour. A guild opts in by running !set_admin_roles / !set_organiser_roles,
-- after which only the listed ids grant the tier and a role merely *named* `admin` grants
-- nothing.

ALTER TABLE `guilds`
  ADD COLUMN `admin_role_ids` TEXT NULL DEFAULT NULL,
  ADD COLUMN `organiser_role_ids` TEXT NULL DEFAULT NULL;

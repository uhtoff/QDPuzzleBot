-- 049_hunt_access_log.sql
-- Slice 5 of docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md:
-- a durable access log for team servers, plus a stored moderator invite code
-- (plan docs/superpowers/plans/2026-10-31-access-log.md).
--
-- hunt_access_log -- one row per access change on a hunt's team server (grant,
-- revoke, elevate, demote, arrive, depart), with how it happened (via) and who did
-- it when known (actor_id). hunt_id deliberately has NO foreign key: the record
-- must outlive a deleted hunt. username is a snapshot, as the member may be gone.
--
-- hunts.mod_invite_code -- the reusable moderator invite for the hunt's team
-- server, made by !mod_invite. Written only by HuntJsonDb.set_mod_invite_code and
-- read only by SQL: HuntData has no such field, so the generic commit() can never
-- clear it.
--
-- PRE-FLIGHT: none needed. The table is new, and the column is additive with a
-- NULL default that is right for every existing row (no mod invite exists yet).
--
-- Re-runnable only in the sense that a second apply fails loudly on the
-- duplicate column.

CREATE TABLE IF NOT EXISTS `hunt_access_log` (
  `id`         BIGINT(20)          NOT NULL AUTO_INCREMENT,
  `created_at` DATETIME            NOT NULL,
  `hunt_id`    INT(11)             NOT NULL,
  `user_id`    BIGINT(20) UNSIGNED NOT NULL,
  `username`   VARCHAR(255)        NULL,
  `action`     VARCHAR(16)         NOT NULL,
  `via`        VARCHAR(16)         NOT NULL,
  `actor_id`   BIGINT(20) UNSIGNED NULL,
  `detail`     VARCHAR(255)        NULL,
  PRIMARY KEY (`id`),
  KEY `idx_hunt_access_log_hunt_created` (`hunt_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

ALTER TABLE `hunts`
  ADD COLUMN `mod_invite_code` VARCHAR(32) NULL DEFAULT NULL;

-- 048_team_invites.sql
-- Slice 4 of docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md:
-- private team-server invites (plan docs/superpowers/plans/2026-10-30-team-invites.md).
--
-- hunt_invites -- one row per single-use invite DM'd to a holder of a hunt's hub
-- role (re-issues add rows). A record, not a gate: the team role is granted on
-- arrival by the hub role alone. It answers "already invited?" so nobody is DM'd
-- twice, and joined_at shows who hasn't arrived. Rows go with their hunt.
--
-- hunts.invites_sent_time -- stamped by the first completed !send_invites for a
-- hunt; switches on automatic DMs to people given the hub role later. Written
-- only by HuntJsonDb.mark_invites_sent and read only by SQL: HuntData has no such
-- field, so the generic commit() can never clear it.
--
-- PRE-FLIGHT: none needed. The table is new, and the column is additive with a
-- NULL default that is right for every existing row by construction --
-- !send_invites does not exist before this migration, so no hunt has sent
-- invites.
--
-- Re-runnable only in the sense that a second apply fails loudly on the
-- duplicate column.

CREATE TABLE IF NOT EXISTS `hunt_invites` (
  `id`         INT(11)             NOT NULL AUTO_INCREMENT,
  `hunt_id`    INT(11)             NOT NULL,
  `user_id`    BIGINT(20) UNSIGNED NOT NULL,
  `code`       VARCHAR(32)         NOT NULL,
  `created_at` DATETIME            NOT NULL,
  `joined_at`  DATETIME            DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_hunt_invites_hunt_user` (`hunt_id`, `user_id`),
  CONSTRAINT `HuntInviteHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

ALTER TABLE `hunts`
  ADD COLUMN `invites_sent_time` DATETIME NULL DEFAULT NULL;

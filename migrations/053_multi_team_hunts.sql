-- 053_multi_team_hunts.sql
-- Multi-team hunts: several teams' hunts on one hub guild share a named hub
-- entity, so the hub can offer a no-spoiler channel, a finishers channel and one
-- combined archive (spec docs/superpowers/specs/2026-11-03-multi-team-hub-channels-design.md).
--
-- multi_team_hunts -- one row per multi-team hunt on a hub guild: its name, the
-- two live hub channels (NULL until created) and when it was archived (NULL while
-- running).
--
-- multi_team_members -- links a team's hunt to its multi-team hunt (a hunt is in
-- at most one) and records when that team finished (NULL = not finished). Rows go
-- with their hunt or their multi-team hunt.
--
-- PRE-FLIGHT: none needed. Both tables are new; no existing table changes.

CREATE TABLE IF NOT EXISTS `multi_team_hunts` (
  `id`                    INT(11)             NOT NULL AUTO_INCREMENT,
  `hub_guild_id`          BIGINT(20) UNSIGNED NOT NULL,
  `name`                  VARCHAR(255)        NOT NULL,
  `no_spoiler_channel_id` BIGINT(20) UNSIGNED NULL DEFAULT NULL,
  `finishers_channel_id`  BIGINT(20) UNSIGNED NULL DEFAULT NULL,
  `created_at`            DATETIME            NOT NULL,
  `archived_time`         DATETIME            NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_multi_team_hunts_hub_archived` (`hub_guild_id`, `archived_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `multi_team_members` (
  `hunt_id`         INT(11)  NOT NULL,
  `multi_team_id`   INT(11)  NOT NULL,
  `completed_time`  DATETIME NULL DEFAULT NULL,
  PRIMARY KEY (`hunt_id`),
  KEY `idx_multi_team_members_mt` (`multi_team_id`),
  CONSTRAINT `MultiTeamMemberHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `MultiTeamMemberMultiTeamID` FOREIGN KEY (`multi_team_id`)
      REFERENCES `multi_team_hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

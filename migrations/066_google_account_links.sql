-- 066_google_account_links.sql
-- Optional Google account linking for named Google Sheets cursors.
--
-- user_google_links -- maps a Discord user id to their verified Google account
-- (OIDC sub + normalized email). Stores NO OAuth tokens: linking only proves
-- ownership of the Google account so Puzzbot can share hunt Drive targets with
-- that email via its own service account.
--
-- drive_user_shares -- ledger of Drive permissions granted by Puzzbot for linked
-- users, storing the Drive permission_id so re-loads are a zero-API-call cache
-- hit, account switches can revoke stale permissions, and unlink can best-effort
-- revoke active-hunt permissions. Rows go with their hunt (ON DELETE CASCADE).
--
-- PRE-FLIGHT: none needed — both tables are new (CREATE TABLE IF NOT EXISTS).
--
-- POST-CHECK:
--   SHOW TABLES LIKE 'user_google_links';
--   SHOW TABLES LIKE 'drive_user_shares';

CREATE TABLE IF NOT EXISTS `user_google_links` (
  `discord_user_id` BIGINT(20) UNSIGNED NOT NULL,
  `google_sub`      VARCHAR(255)        NOT NULL,
  `google_email`    VARCHAR(320)        NOT NULL,
  `linked_at`       DATETIME            NOT NULL,
  PRIMARY KEY (`discord_user_id`),
  KEY `idx_user_google_links_email` (`google_email`(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `drive_user_shares` (
  `id`              INT(11)             NOT NULL AUTO_INCREMENT,
  `hunt_id`         INT(11)             NOT NULL,
  `discord_user_id` BIGINT(20) UNSIGNED NOT NULL,
  `drive_file_id`   VARCHAR(128)        NOT NULL,
  `google_email`    VARCHAR(320)        NOT NULL,
  `permission_id`   VARCHAR(128)        NOT NULL,
  `shared_at`       DATETIME            NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_drive_share_user_file` (`discord_user_id`, `drive_file_id`),
  KEY `idx_drive_share_hunt` (`hunt_id`),
  KEY `idx_drive_share_file_email` (`drive_file_id`, `google_email`(191)),
  CONSTRAINT `DriveUserShareHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

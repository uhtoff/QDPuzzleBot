-- Dashboard SPA schema additions (Phase 1).
-- All changes are additive and backward-compatible with the running bot:
-- existing columns/queries are untouched. Run before deploying the new code.

-- Hunt lifecycle: active (0) -> archived (1). Archived hunts appear in the
-- cross-team "Old hunts" menu, gated by guild membership only.
ALTER TABLE `hunts`
    ADD COLUMN `archived` TINYINT(1) NOT NULL DEFAULT 0;

-- Last non-bot (human) message seen per channel, fed by an on_message listener.
-- Used by the dashboard's "recent activity" view without slow history fetches.
CREATE TABLE IF NOT EXISTS `channel_activity` (
    `channel_id`            BIGINT UNSIGNED NOT NULL,
    `guild_id`              BIGINT UNSIGNED NOT NULL,
    `last_human_message_at` DATETIME        NOT NULL,
    `last_author_id`        BIGINT UNSIGNED NOT NULL,
    `last_author_name`      VARCHAR(255)    NOT NULL DEFAULT '',
    PRIMARY KEY (`channel_id`),
    KEY `idx_channel_activity_guild` (`guild_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

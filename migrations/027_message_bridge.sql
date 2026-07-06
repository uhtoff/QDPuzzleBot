-- Discord -> web message bridge (forward-only capture of hunt puzzle channels).
-- Additive: existing tables/queries are untouched. Ship before deploying the code.

CREATE TABLE IF NOT EXISTS `messages` (
    `id`            BIGINT UNSIGNED NOT NULL,   -- Discord message snowflake (time-sortable)
    `channel_id`    BIGINT UNSIGNED NOT NULL,
    `guild_id`      BIGINT UNSIGNED NOT NULL,
    `author_id`     BIGINT UNSIGNED NOT NULL,
    `author_name`   VARCHAR(255)    NOT NULL DEFAULT '',
    `author_avatar` VARCHAR(255)    NULL,
    `kind`          TINYINT         NOT NULL DEFAULT 0,   -- 0 human,1 this-bot,2 other-bot,3 command
    `content`       TEXT            NULL,
    `reference_id`  BIGINT UNSIGNED NULL,
    `created_at`    DATETIME(3)     NOT NULL,
    `edited_at`     DATETIME(3)     NULL,
    PRIMARY KEY (`id`),
    KEY `idx_messages_channel` (`channel_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `message_attachments` (
    `id`              BIGINT UNSIGNED NOT NULL,   -- Discord attachment snowflake
    `message_id`      BIGINT UNSIGNED NOT NULL,
    `filename`        VARCHAR(255)    NOT NULL DEFAULT '',
    `content_type`    VARCHAR(100)    NULL,
    `size`            INT             NOT NULL DEFAULT 0,
    `source_url`      VARCHAR(512)    NOT NULL DEFAULT '',
    `local_path`      VARCHAR(512)    NULL,
    `download_status` TINYINT         NOT NULL DEFAULT 0,   -- 0 pending,1 done,2 failed
    PRIMARY KEY (`id`),
    KEY `idx_message_attachments_msg` (`message_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

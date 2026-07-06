-- Message bridge: capture Discord embeds (solve/partial announcements are embed-only).
-- Additive: nullable JSON payload; existing rows/queries untouched.

ALTER TABLE `messages`
    ADD COLUMN `embeds` TEXT NULL AFTER `content`;

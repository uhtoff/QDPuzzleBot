-- 061_canvas_enabled.sql
-- Per-hunt switch for the dashboard's Canvas view (organisers turn it on/off).
--   hunts.canvas_enabled   0/1. New hunts start off. An existing hunt that
--                          already has a canvas is backfilled on, so nobody
--                          loses a canvas they are using; the rest stay off.
-- Turning it off hides the view; the canvases themselves are kept.
--
-- PRE-FLIGHT: run in EVERY environment before pushing (a defaulted column does
-- not prove itself by applying):
--   SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
--     AND TABLE_NAME='hunts' AND COLUMN_NAME='canvas_enabled';
-- must return 0.
--
-- POST-CHECK:
--   SELECT canvas_enabled, COUNT(*) FROM hunts GROUP BY canvas_enabled;
--   SELECT COUNT(DISTINCT hunt_id) FROM hunt_canvases;
--     -> the canvas_enabled=1 count equals the number of hunts with a canvas
--   SHOW COLUMNS FROM hunts LIKE 'canvas_enabled';
--     -> Default 0
--
-- Plain ADD COLUMN (not MariaDB's IF NOT EXISTS, which CI's MySQL 8 rejects):
-- migrate.py records each migration in schema_migrations and applies it once.
ALTER TABLE `hunts` ADD COLUMN `canvas_enabled` TINYINT(1) NOT NULL DEFAULT 0;
UPDATE `hunts` SET `canvas_enabled` = 1
  WHERE `id` IN (SELECT `hunt_id` FROM `hunt_canvases`);

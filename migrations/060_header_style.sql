-- 060_header_style.sql
-- Per-hunt puzzle-sheet header style, and the layout each puzzle was made with.
--   hunts.header_style     'full' or 'compact'. Existing hunts are backfilled
--                          'full'; the column default is then switched to
--                          'compact' so a NEW hunt row is compact.
--   puzzles.header_layout  'full' or 'compact': the layout this puzzle's sheet
--                          was created with, written once at insert. Existing
--                          puzzles are 'full'.
--
-- PRE-FLIGHT: run in EVERY environment before pushing (a defaulted column does
-- not prove itself by applying):
--   SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
--     AND ((TABLE_NAME='hunts' AND COLUMN_NAME='header_style')
--       OR (TABLE_NAME='puzzles' AND COLUMN_NAME='header_layout'));
-- must return 0.
--
-- POST-CHECK:
--   SELECT header_style, COUNT(*) FROM hunts GROUP BY header_style;
--     -> only 'full'
--   SHOW COLUMNS FROM hunts LIKE 'header_style';
--     -> Default 'compact'
--   SELECT header_layout, COUNT(*) FROM puzzles GROUP BY header_layout;
--     -> only 'full'
--
-- Plain ADD COLUMN (not MariaDB's IF NOT EXISTS, which CI's MySQL 8 rejects):
-- migrate.py records each migration in schema_migrations and applies it once.
ALTER TABLE `hunts` ADD COLUMN `header_style` VARCHAR(10) NOT NULL DEFAULT 'full';
ALTER TABLE `hunts` ALTER COLUMN `header_style` SET DEFAULT 'compact';
ALTER TABLE `puzzles` ADD COLUMN `header_layout` VARCHAR(10) NOT NULL DEFAULT 'full';

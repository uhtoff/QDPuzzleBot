-- 015_hunt_layout_mode.sql
-- Per-hunt option to use forum-backed layout instead of channel-based.
ALTER TABLE hunts ADD COLUMN layout_mode VARCHAR(20) NOT NULL DEFAULT 'channel';

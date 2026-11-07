-- 058_drop_dead_columns.sql
-- Step two of retiring three columns nothing reads or writes any more:
--   channel_activity.last_author_id  (057 gave it a default; readers use last_author_name)
--   guilds.drive_resources_id        (no feature ever read it)
--   hunts.layout_mode                (the forum layout was removed)
--
-- PRE-FLIGHT: ship ONLY after e89dbd9 (code stops writing these) is live on
-- BOTH the test and live instances. Before that, the running process can still
-- write a column in the migrate -> restart window and fail.

ALTER TABLE `channel_activity`
  DROP COLUMN `last_author_id`;

ALTER TABLE `guilds`
  DROP COLUMN `drive_resources_id`;

ALTER TABLE `hunts`
  DROP COLUMN `layout_mode`;

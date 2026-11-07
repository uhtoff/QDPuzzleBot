-- 057_channel_activity_author_id_default.sql
-- Step one of retiring channel_activity.last_author_id (written, never read:
-- every reader uses last_author_name). The code stops writing it in the same
-- push, and the column was NOT NULL with no default, so without this the
-- upsert behind every human message fails with "Field 'last_author_id'
-- doesn't have a default value". A later migration drops the column once this
-- code is live on both instances.
--
-- PRE-FLIGHT: none needed. Only adds a default; old code that still writes the
-- column is unaffected, so it is safe in the migrate -> restart window.

ALTER TABLE `channel_activity`
  ALTER COLUMN `last_author_id` SET DEFAULT 0;

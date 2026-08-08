-- 038_hunt_category_fk.sql
-- T3 (docs/reviews/2026-08-07-comprehensive-review.md): 026_hunt_category.sql repeats
-- the exact bug 022 diagnosed and fixed for tag_group_category. hunt_id was declared
-- BIGINT(20) UNSIGNED -- copy-pasted from category_id, which legitimately holds a
-- Discord snowflake -- but it actually holds an internal hunts.id, which is int(11).
-- A FK requires identical column types (errno 150 otherwise), so no FK was ever added
-- and hunt deletion never cascaded here.
--
-- The app-side cleanup that stood in for the cascade sits inside `if guild is not None:`
-- (puzzle_service.py), so a guild-less hunt delete -- every web/API delete -- orphans
-- the rows, and a recycled Discord category can then be mis-adopted by a later hunt.
--
-- Orphan sweep first, as in 022/037: an existing orphan would violate the FK. Verified
-- 0 orphans on both puzzlebot_test and production (puzzbot_new) before writing this, so
-- the DELETE is expected to be a no-op -- it stays because other environments may differ
-- and it makes the migration safe to re-run.
--
-- The narrowing MODIFY is data-safe here: production holds 3 rows, all hunt_id=111, no
-- NULLs, so every value fits int(11) comfortably. (022 got away with the same MODIFY
-- because its table was empty at the time; this one is not, hence the check.)

DELETE hc FROM hunt_category hc
  LEFT JOIN hunts h ON hc.hunt_id = h.id
  WHERE h.id IS NULL;

ALTER TABLE `hunt_category`
  MODIFY COLUMN `hunt_id` int(11) NOT NULL;

ALTER TABLE `hunt_category`
  ADD CONSTRAINT `HuntCategoryHuntID` FOREIGN KEY (`hunt_id`)
      REFERENCES `hunts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;

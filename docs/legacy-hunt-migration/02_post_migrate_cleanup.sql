-- 02_post_migrate_cleanup.sql
-- Post-migration cleanup, run AFTER `migrate.py migrate` has applied 000..024.
-- Runs against the CURRENT schema (tables: tag_group, tags, puzzles) — note the
-- renamed columns: tag_group.target_puzzle_id, tags.tag_group_id, tags.edge_type.
--
-- Covers the gaps the numbered migrations do not: NULL group types, targets with no
-- self-membership row, the missing baseline `locked` column, and orphan-puzzle junk.

-- 1. Backfill group `type`. Migration 016 only maps the code-written values
--    'Round'/'Metaless Round'; legacy rows left NULL are typed by structure:
--    has a target -> Metapuzzle, otherwise -> Group. (All 92 legacy NULLs were
--    targetless and became Group; the CASE keeps the script correct in general.)
UPDATE `tag_group`
SET `type` = CASE
    WHEN `target_puzzle_id` IS NOT NULL AND `target_puzzle_id` <> 0 THEN 'Metapuzzle'
    ELSE 'Group'
  END
WHERE `type` IS NULL;

-- 2a. Normalise any self-membership row (puzzle == its group's target) to the
--     'target' edge, in case one slipped through as 'feeds'/'none'. (None did.)
UPDATE `tags` t
JOIN `tag_group` g ON g.`id` = t.`tag_group_id`
SET t.`edge_type` = 'target'
WHERE t.`puzzle_id` = g.`target_puzzle_id`
  AND t.`edge_type` <> 'target';

-- 2b. Insert a 'target' self-edge for targets that had NO self-membership row at
--     all (9 in the legacy data). Target identity is carried by
--     tag_group.target_puzzle_id regardless, but this keeps them consistent with
--     the other targets and with the 018/019 convention.
INSERT INTO `tags` (`tag_group_id`, `puzzle_id`, `edge_type`)
SELECT g.`id`, g.`target_puzzle_id`, 'target'
FROM `tag_group` g
WHERE g.`target_puzzle_id` IS NOT NULL AND g.`target_puzzle_id` <> 0
  AND NOT EXISTS (
    SELECT 1 FROM `tags` t
    WHERE t.`tag_group_id` = g.`id` AND t.`puzzle_id` = g.`target_puzzle_id`
  );

-- 3. Add the baseline `locked` column absent from this legacy DB (a dead column
--    kept for schema fidelity; no current code reads/writes it).
--    NOT idempotent — skip if the column already exists.
ALTER TABLE `puzzles` ADD COLUMN `locked` tinyint(1) NOT NULL DEFAULT 0 AFTER `status`;

-- 4. Junk sweep: orphan puzzles with no hunt and no tag (1368 rows) — leftovers of
--    previously-deleted hunts (puzzles never cascade-delete in the baseline).
DELETE FROM `puzzles`
WHERE `hunt_id` IS NULL
  AND `id` NOT IN (SELECT `puzzle_id` FROM `tags`);

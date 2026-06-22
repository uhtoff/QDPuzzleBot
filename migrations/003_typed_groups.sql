-- M6: additive typed-group columns on `rounds` + `tags`.
-- Lets a `rounds` row be reasoned about as a typed TagGroup without renaming the
-- table or the load-bearing `round_id` membership key. Additive and
-- backward-compatible: no existing column/query changes. Run before deploying the
-- M6 code (the generic commit() writes every dataclass field by name).
--
-- kind / status are backfilled here and kept-updated by the M6 code, but read by
-- nothing for behaviour yet. parent_group_id (group-in-group NESTING) and
-- tags.edge_type land dormant at their defaults for M7. category_id is already
-- nullable, so it needs no change.

ALTER TABLE `rounds`
  ADD COLUMN `kind`            varchar(20) DEFAULT NULL,
  ADD COLUMN `status`          varchar(20) NOT NULL DEFAULT 'active',
  ADD COLUMN `parent_group_id` int(11)     DEFAULT NULL,
  ADD CONSTRAINT `ParentGroupID` FOREIGN KEY (`parent_group_id`)
      REFERENCES `rounds` (`id`) ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE `tags`
  ADD COLUMN `edge_type` varchar(32) NOT NULL DEFAULT 'feeds-meta';

-- Backfill kind (order matters: metaless -> meta -> metameta override).
UPDATE `rounds` SET `kind` = 'metaless' WHERE `meta_id` IS NULL OR `meta_id` = 0;

UPDATE `rounds` SET `kind` = 'meta' WHERE `meta_id` IS NOT NULL AND `meta_id` <> 0;

UPDATE `rounds` r JOIN `puzzles` p ON p.`id` = r.`meta_id`
  SET r.`kind` = 'metameta' WHERE p.`metameta` = 1;

-- Backfill status from the round's lifecycle timestamps. The round's solve_time is
-- stamped when its meta solves, so solve_time set => the round is solved.
UPDATE `rounds` SET `status` = CASE
  WHEN `archive_time` IS NOT NULL THEN 'archived'
  WHEN `solve_time`   IS NOT NULL THEN 'solved'
  ELSE 'active'
END;

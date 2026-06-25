-- 012_edge_model.sql
-- Rewrite edge vocabulary: feeds-meta→feeds, aggregates-into→none.
-- Retarget column defaults. Drop now-derived kind column.

-- 1. Rewrite stored edge values (run before default change so no
--    in-flight insert lands an old value between steps).
UPDATE `tags`    SET `edge_type` = 'feeds' WHERE `edge_type` = 'feeds-meta';
UPDATE `tags`    SET `edge_type` = 'none'  WHERE `edge_type` = 'aggregates-into';
UPDATE `rollups` SET `edge_type_filter` = 'feeds'
                 WHERE `edge_type_filter` = 'feeds-meta';

-- 2. Retarget column defaults so new rows speak the new vocabulary.
ALTER TABLE `tags`
  ALTER COLUMN `edge_type` SET DEFAULT 'feeds';
ALTER TABLE `rollups`
  ALTER COLUMN `edge_type_filter` SET DEFAULT 'feeds';

-- 3. Drop kind — now derived (has-target = target_puzzle_id IS NOT NULL;
--    fed-vs-informed from incoming edges; metameta from structure).
ALTER TABLE `tag_group`
  DROP COLUMN `kind`;

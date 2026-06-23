-- M7: enforce one membership edge per (round_id, puzzle_id).
-- Dedup first (information-safe while all rows are still default edge_type='feeds-meta'),
-- then swap the non-unique key for a UNIQUE CONSTRAINT so the upsert edge writer can
-- use ON DUPLICATE KEY UPDATE.

DELETE t1 FROM tags t1 JOIN tags t2
  ON t1.round_id = t2.round_id AND t1.puzzle_id = t2.puzzle_id AND t1.id > t2.id;

ALTER TABLE tags DROP KEY tag_index, ADD UNIQUE KEY tag_uq (round_id, puzzle_id);

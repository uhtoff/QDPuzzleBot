-- M7: backfill source_mode for metameta rounds.
-- Sets source_mode='subtree' on rollups whose meta puzzle has metameta=1,
-- so that after the engine refactor (Phase 4) the old metameta path is
-- preserved via source_mode instead of reading the flag directly.

UPDATE rollups r
JOIN rounds rd ON r.round_id = rd.id
JOIN puzzles p ON rd.meta_id = p.id
SET r.source_mode = 'subtree'
WHERE p.metameta = 1;

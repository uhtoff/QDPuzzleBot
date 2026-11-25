-- 065_puzzle_answers_backfill.sql
-- Split each puzzles.solution ('A/B/C', the tick '✅' never stored as a row) into
-- puzzle_answers rows and set puzzles.expected_answers.
--
-- Re-runnable only until stage 2 ships: the UPDATEs below would overwrite counts people have set.
--
-- PRE-FLIGHT, all must hold before pushing (run in EVERY environment):
--   SELECT MAX(LENGTH(solution)-LENGTH(REPLACE(solution,'/',''))+1) FROM puzzles;   -> <= 20
--   SELECT MAX(CHAR_LENGTH(solution)) FROM puzzles;                                 -> <= 250
--   SELECT COUNT(*) FROM puzzles WHERE solved = 1 AND (solution IS NULL OR solution = '');
--     -> note the number: these become expected 0 and read the tick after their next answer write.
--
-- POST-CHECK, both must return 0 (or every returned row explained by an empty middle
-- fragment such as 'A//B'):
--   SELECT COUNT(*) FROM puzzles p WHERE p.solution <> '' AND p.solution <> '✅'
--     AND NOT EXISTS (SELECT 1 FROM puzzle_answers a WHERE a.puzzle_id = p.id);
--   SELECT COUNT(*) FROM puzzles p JOIN (SELECT puzzle_id,
--       GROUP_CONCAT(answer ORDER BY position SEPARATOR '/') j
--       FROM puzzle_answers GROUP BY puzzle_id) a ON a.puzzle_id = p.id
--     WHERE REPLACE(a.j,' ','') <> REPLACE(TRIM(BOTH '/' FROM p.solution),' ','');
INSERT INTO `puzzle_answers` (`puzzle_id`, `answer`, `position`, `created_at`)
SELECT f.id, f.frag, ROW_NUMBER() OVER (PARTITION BY f.id ORDER BY f.n), f.solve_time
FROM (
  SELECT p.id, p.solve_time, nums.n,
         TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(p.solution, '/', nums.n), '/', -1)) AS frag
  FROM `puzzles` p
  JOIN (
    SELECT 1 n UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5
    UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10
    UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL SELECT 13 UNION ALL SELECT 14 UNION ALL SELECT 15
    UNION ALL SELECT 16 UNION ALL SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19 UNION ALL SELECT 20
  ) nums ON nums.n <= LENGTH(p.solution) - LENGTH(REPLACE(p.solution, '/', '')) + 1
  WHERE p.solution IS NOT NULL AND p.solution <> ''
    AND NOT EXISTS (SELECT 1 FROM `puzzle_answers` a WHERE a.puzzle_id = p.id)
) f
WHERE f.frag <> '' AND f.frag <> '✅';

UPDATE `puzzles` p
SET p.expected_answers = (SELECT COUNT(*) FROM `puzzle_answers` a WHERE a.puzzle_id = p.id)
WHERE p.solved = 1;

UPDATE `puzzles` p
SET p.expected_answers = (SELECT COUNT(*) FROM `puzzle_answers` a WHERE a.puzzle_id = p.id) + 1,
    p.expected_answers_confirmed = 0
WHERE p.solved = 0
  AND EXISTS (SELECT 1 FROM `puzzle_answers` a WHERE a.puzzle_id = p.id);

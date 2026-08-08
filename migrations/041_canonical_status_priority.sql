-- 041_canonical_status_priority.sql
-- "Status vocabulary drift" (docs/reviews/2026-08-07-comprehensive-review.md).
--
-- bot/core/constants.py declares STATUSES and PRIORITIES in lowercase and is
-- the single source of truth, but nothing enforced that on the way into the
-- column. update_puzzle_field validated case-INsensitively and then stored the
-- caller's spelling verbatim, and the callers disagree: the slash choices and
-- the dashboard picker are user-facing labels ("In Progress", "Very High"),
-- while a bang command sends whatever the user typed. The service's own
-- solve/unsolve/partial paths wrote Title Case literals too. So the column
-- accumulated both spellings of the same status.
--
-- The application half landed alongside this migration: update_puzzle_field
-- lowercases on write, the service writes named lowercase constants, and the
-- dashboard sends stored values while title-casing only for display. This
-- migration is the other half -- without it "consistent" would be true only of
-- rows written from now on, and PuzzleTable's status filter, which builds its
-- options from the DISTINCT stored values, would keep offering "Stuck" and
-- "stuck" as two separate filters.
--
-- Two rewrites, not one:
--
--   1. LOWER() on status and priority. Pure case folding, no value changes
--      meaning. This also folds the two statuses that were never in STATUSES
--      at all -- "Unsolved" and "Partially Solved", written by unsolve() and
--      add_partial() -- onto the UNSOLVED_STATUS / PARTIAL_STATUS constants
--      those paths now write.
--
--   2. priority 'Normal' -> 'medium'. This one DOES change a value, and is the
--      only semantic rewrite here. Puzzle creation wrote priority='Normal'
--      (puzzle_service.py:2462,2673) but PRIORITIES has never contained
--      "normal" -- so every puzzle was created with a priority its own
--      validator rejects and no picker offers. 'medium' is the in-vocabulary
--      equivalent, and priorityColor renders both through the same default
--      branch, so no badge changes colour.
--
-- PRE-FLIGHT counts on puzzlebot_test, 2026-08-08 (205 puzzle rows):
--   status:   Unstarted 100, NULL 59, Solved 35, Unsolved 10, In Progress 1
--             -> 146 rows rewritten by (1)
--   priority: Normal 147, NULL 58
--             -> 147 rows rewritten by (1)+(2)
--
-- Re-run this before applying to any other environment -- production counts
-- were NOT taken (no authorised DB access from this session):
--
--   SELECT status, COUNT(*) FROM puzzles GROUP BY status;
--   SELECT priority, COUNT(*) FROM puzzles GROUP BY priority;
--
-- Anything in those results outside STATUSES + {unsolved, partially solved} /
-- PRIORITIES, once lowercased, is a value this migration does not know about:
-- decide what it maps to before applying, rather than letting it through as an
-- unrecognised lowercase string.
--
-- Both statements are idempotent -- re-running rewrites nothing.

UPDATE `puzzles`
   SET `status` = LOWER(`status`)
 WHERE `status` IS NOT NULL
   AND BINARY `status` <> LOWER(`status`);

UPDATE `puzzles`
   SET `priority` = LOWER(`priority`)
 WHERE `priority` IS NOT NULL
   AND BINARY `priority` <> LOWER(`priority`);

UPDATE `puzzles`
   SET `priority` = 'medium'
 WHERE `priority` = 'normal';

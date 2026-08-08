# Changed Files

## Git status

On `staging`, clean before this tranche. All changes below are this tranche.

## Files changed

Vocabulary + service:
- `bot/core/constants.py` — `UNSOLVED_STATUS`, `PARTIAL_STATUS`, `ALL_STATUS_VALUES`,
  `format_status`, `format_priority`.
- `bot/core/puzzle_service.py` — 3 status write sites → constants/lowercase; 2 creation
  defaults (`status='unstarted'`, `priority='medium'`); `update_puzzle_field` lowercases
  status and priority on write.
- `bot/cogs/puzzles.py` — slash choices derived from `STATUSES` (Title Case name,
  lowercase value); docstring; 5 display sites formatted.

Frontend:
- `frontend/src/lib/statusLabel.ts` (new) + `statusLabel.test.ts` (new)
- `frontend/src/util.ts` — re-exports the formatters
- `PuzzleEditCells.tsx` (options now stored values), `PuzzleTable.tsx` (badges +
  filter labels), `BoardGroup.tsx`, `DragBoard.tsx`, `PuzzleDrawer.tsx`, `api.ts`

Data + docs:
- `migrations/041_canonical_status_priority.sql` (new)
- `docs/commands.md`, `docs/user-guide.md`, `docs/reviews/2026-08-07-comprehensive-review.md`

Tests:
- `tests/test_core_constants.py` — 4 new vocabulary guards (choices-match-STATUSES was
  mutation-proven: `STATUSES[:-1]` made it fail on the values comparison, then restored)
- `tests/test_update_puzzle_field.py` — 2 new canonicalisation tests; 1 contract updated
- `tests/test_minimeta_service.py`, `tests/test_solve_equivalence.py` — 3 pinned status
  strings updated to lowercase (deliberate contract change, reason recorded at each site)

## Commands run

    timeout 300 pipenv run python -m pytest tests/ -q     # 1816 passed
    timeout 180 pipenv run ruff check                     # All checks passed
    timeout 300 pipenv run basedpyright bot/              # 0 errors
    timeout 120 pipenv run python migrate.py migrate      # applied 041
    cd frontend && npm test                               # 32 passed
    cd frontend && npm run build                          # clean

## Tests not run

None skipped. No test runner exists for the Discord embed rendering itself — the
display formatting there is inspection-only, verified by reading the call sites.

## Current risks

- Migration 041 unverified against production (see session-summary Open questions).
- `statusTone` still tints `"partially solved"` green; logged as a new open finding,
  deliberately unchanged.

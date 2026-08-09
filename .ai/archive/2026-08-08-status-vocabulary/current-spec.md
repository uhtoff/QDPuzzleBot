# Current Spec

## Goal

Close the **status vocabulary drift** finding from the 2026-08-07 comprehensive
review (`docs/reviews/2026-08-07-comprehensive-review.md`, "Still open" line 127).

Puzzle status has three separate drifts:

1. `"needs submission"` is in `STATUSES` and in the dashboard picker, but missing
   from the `!status` slash choices, its docstring, and both docs files.
2. `unsolve()` writes `"Unsolved"` and `add_partial()` writes `"Partially Solved"` —
   neither is in `STATUSES`, so the service writes values its own validator
   (`puzzle_service.py:1895`) would reject. (`"Partially Solved"` was NOT in the
   review; found while mapping write sites.)
3. Case drift: `STATUSES` is lowercase, but the slash choices, dashboard picker,
   `solve()` and `unsolve()` all write Title Case. Validation is case-insensitive,
   so stored data is genuinely mixed-case today.

## Decisions (user, 2026-08-08)

- **`"Unsolved"` / `"Partially Solved"` become named non-settable constants**, not
  members of `STATUSES`. `STATUSES` stays the *user-settable* vocabulary, so
  `update_puzzle_field` keeps rejecting them — which matches the existing message
  telling the user to use the unsolve command.
- **Canonical stored case is lowercase**, matching `STATUSES` (already the declared
  single source of truth, pinned by `tests/test_core_constants.py`). Title Case is
  produced at the *display edges* by a formatter.
- **Existing rows get a backfill migration** (041). Without it the drift survives in
  old rows and `PuzzleTable`'s status filter — which builds options from distinct
  stored values — shows "Stuck" and "stuck" as separate entries.

## In scope

- `bot/core/constants.py` — add `UNSOLVED_STATUS`, `PARTIAL_STATUS`.
- `bot/core/puzzle_service.py` — `:1258` `"Solved"`→`"solved"`, `:1332`→`UNSOLVED_STATUS`,
  `:1815`→`PARTIAL_STATUS`; `update_puzzle_field` lowercases on write so web/slash
  Title Case input stores canonically.
- `bot/cogs/puzzles.py` — slash choice *values* lowercase, add the missing
  Needs Submission choice, fix the docstring; format for display in the embed/list
  sites (`:2755`, `:3009`, `:3190`).
- `frontend/src/util.ts` — `formatStatus()`; call sites in `PuzzleEditCells`,
  `BoardGroup`, `DragBoard`, `PuzzleDrawer`, `PuzzleTable`; picker options become
  value/label pairs; `api.ts:381` optimistic `"Solved"`→`"solved"`.
- `docs/commands.md:87`, `docs/user-guide.md:76`.
- `migrations/041_*.sql` — normalise existing `puzzles.status`.

## Out of scope

- `target_group.status` (`:1288`) and `round_.status` (`:1386`) — a different field
  with its own vocabulary.
- `statusTone` giving `"partially solved"` a green/solved tone (it contains
  "solved"). Pre-existing, arguably intended; flag in the review doc, do not change.
- The other five open review findings (rate limiting, keyboard a11y,
  `tag_group.group_code`, the unsolve embed pair, `backfill_hunt_uids.py`).

## Constraints

- `"Partially Solved"` is pinned as an exact string by
  `tests/test_solve_equivalence.py:459` and `test_solve_puzzle_web_invariants.py`.
  Updating them is a deliberate contract change — state the reason, and check the
  assertion does not get weaker.
- `statusTone` matches a lowercased **substring** deliberately (bot wrote lowercase,
  dashboard wrote Title Case). Keep the `unsolved`-excluding carve-out at
  `statusTone.ts:20` — it is a twice-fixed regression.
- Migration 041: verify affected row counts on dev *and* production before shipping,
  as was done for 040.

## Acceptance criteria

- Every puzzle-status write in `bot/` produces a lowercase canonical value.
- `STATUSES` + `UNSOLVED_STATUS` + `PARTIAL_STATUS` is the complete stored vocabulary.
- Slash choices, docstring, and both docs list all seven settable statuses.
- Users still see Title Case in Discord embeds and dashboard badges.
- `timeout 180 pipenv run python -m pytest tests/ -q` green.
- `cd frontend && npm test` and `npm run build` green.
- `pipenv run ruff check` and `pipenv run basedpyright bot/` clean.

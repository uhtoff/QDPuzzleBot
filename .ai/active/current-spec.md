# Current Spec — Phase 1: Solve panel completeness

Part of `.ai/plans/web-command-parity.md` (web ↔ bang command parity). This is
Phase 1 only.

## Goal

Give the web dashboard the full solve family, not just full solve/unsolve:
`partial`, `change_solution`, `add_solution`, and `mark_as_complete` — matching
the bang commands `!ps`, `!change_solution`, `!add_solution`, `!mark_as_complete`.

## In scope

- Extract the solve-variant mutation logic out of the Puzzles cog into
  `PuzzleService` so the bang edge and the web edge share one seam.
- Web endpoint(s) for partial / change / add / mark-complete.
- Frontend controls in the solve UI.

## Out of scope

- Phases 2–5 (rename, link, clue UI, convert, delete, reconcile, hunt lifecycle).
- Any change to full `solve`/`unsolve` behaviour (already at parity).
- Mini-specific solving (`!solve_mini`) — already reachable via the mini panel.

## Relevant files

- `bot/cogs/puzzles.py` — `_partial_impl` (L2209), `change_solution` (L2300),
  `add_solution` (L2345), `mark_as_complete` (L2386), `_update_all_metas` (L2187),
  `_normalize_solution`.
- `bot/core/puzzle_service.py` — `solve` (L523) is the contract to mirror;
  `refresh_metas` (L366); `_gsheet_cog`.
- `bot/web/actions.py` — `solve_puzzle` (L316) / `unsolve_puzzle` reference; add the
  new route(s) nearby.
- `frontend/src/api.ts` — solve/unsolve mutation hooks.
- `frontend/src/components/` — PuzzleDrawer (solve UI) and MiniPuzzlePanel.
- `tests/test_solve_equivalence.py`, `tests/test_refresh_meta.py`.

## Required behaviour

### 1. Service extraction (refactor — no behaviour change for bang)

Add to `PuzzleService`, each mirroring `solve`'s isolation/return contract (mutate,
`PuzzleJsonDb.commit`, refresh affected metas via `refresh_metas`, raise
`SheetRefreshError(failed)` on sheet failure; the mutation always commits first):

- `add_partial(guild, hunt, puzzle, solution)` — from `_partial_impl`:
  `status = "Partially Solved"`; append `"/"+solution` if a solution exists else
  set it; commit; refresh this puzzle's tagged metas. (No own-sheet push — matches
  current `_partial_impl`, which does not call `update_solution`.)
- `change_solution(guild, hunt, puzzle, solution)` — from `change_solution` body
  (the already-solved branch): set `solution`, `solve_time = now(UTC)`; commit;
  refresh metas; push the puzzle's own sheet via the gsheet cog `update_solution`.
- `add_solution(guild, hunt, puzzle, solution)` — from `add_solution` body
  (already-solved branch): `solution += "/"+solution`; `solve_time = now(UTC)`;
  commit; refresh metas; gsheet `update_solution`.

Rewire the three bang commands to call these methods, keeping their embeds /
channel messages / `_warn_stale_sheets` handling. The "not yet solved" fallbacks
stay in the cog: `change_solution` on an unsolved puzzle → `_solve_impl`;
`add_solution` on an unsolved puzzle → partial. `mark_as_complete` stays
`_solve_impl(ctx, "✅")` and needs no new service method.

### 2. Backend endpoint(s)

Extend the solve route. Preferred: add a `mode` to `POST /api/puzzles/<id>/solve`:
`full` (default, current behaviour) | `partial` | `change` | `add`. `mark`
(mark-as-complete) = `full` with solution `"✅"` (frontend can just send that, so no
separate mode needed). Validate empty-solution per variant the same way the cog
does. Same auth as `solve_puzzle` (guild member, hunt not archived), `puzzle_lock`,
`_audit`, best-effort `refresh_unassigned_safe`, and `{ ok, sheet_warning? }`
response.

### 3. Frontend

In the solve UI (PuzzleDrawer; mirror in MiniPuzzlePanel where it has solve), add
controls for: add partial, change solution, add extra solution, mark complete.
Add the matching `RetagMode`-style mutation calls in `api.ts`. Disable variants
that don't apply to the current solved/unsolved state (e.g. change/add only when
already solved), matching the cog's guards.

## Constraints

- Service methods must keep bang behaviour byte-identical (equivalence suite).
- The mutation commits before sheet refresh; sheet failures are non-fatal warnings.
- Reuse existing auth/audit/board-push patterns; no new auth model.

## Acceptance criteria

- `test_solve_equivalence.py` (extended) shows bang and web produce identical DB
  state for partial / change / add / mark-complete.
- A new service-level test exercises each new `PuzzleService` method (tags refreshed,
  solution/status/solve_time set correctly), mirroring `test_refresh_meta.py`.
- From the dashboard: a solved puzzle can have its solution changed and an extra
  solution appended; an unsolved puzzle can be marked partial and marked complete.
- `tsc --noEmit` clean; targeted pytest green; manual click-through done.

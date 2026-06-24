# Implementation Plan — Unassigned-puzzles rework (review feedback)

Follows the merged-but-not-yet review of the meta-matching "Unclassified Puzzles" branch.
User feedback (2026-06-24):

1. Rename **Unclassified → Unassigned** everywhere incl. DB columns + migration.
2. Web creation = a normal "New puzzle" with **Unassigned** as an option in the modal's
   tag/meta dropdown. Drop the separate button/modal/endpoint/hook.
3. Unassigned puzzles' channels go in the **hunt category** (`hunt.category_id`).
4. Bang `!p` (and any create with no round) must put the channel in the hunt category too
   (currently lands in root).
5. The sheet tab is created but **nothing writes to it** — fix the Phase-5 reconcile.

## Phase 1 — Rename Unclassified → Unassigned (mechanical, incl. DB)
- `bot/core/unclassified.py` → `bot/core/unassigned.py`; `unclassified_row` → `unassigned_row`.
- `migrations/010_unclassified.sql` → `010_unassigned.sql`; columns `unassigned_sheet_page_id`,
  `unassigned_table_id`.
- `hunt_data.py` fields, `mysqldb.get_unclassified` → `get_unassigned`,
  `puzzle_service.refresh_unclassified` → `refresh_unassigned`, gsheet methods
  `ensure/read/apply_unassigned_*`, `puzzles.py` triggers + `!rebuild_unassigned`,
  `actions.py` refresh calls.
- Tests renamed + symbols updated. Sheet tab string → "Unassigned Puzzles".
- **Test DB note:** 010 already applied with old names → ship a one-off
  `ALTER TABLE hunts RENAME COLUMN ...` for the user to run on puzzlebot_test.
- Accept: full suite green, ruff clean.

## Phase 2 — Channel category fallback
- `puzzle_service.create_puzzle`: when `round_data is None`, resolve category from
  `hunt.category_id` instead of `None`. Fixes both bang `!p` and web unassigned creates.
- Accept: suite green; manual smoke later.

## Phase 3 — Fold web creation into "New puzzle"
- `actions.py`: allow empty `meta_code` in `create_puzzle` → `meta_round=None`; delete the
  `/unassigned` endpoint.
- `api.ts`: drop `useCreateUnclassified`; allow `useCreatePuzzle` to send empty meta_code.
- `CreateControls.tsx`: remove the "New unclassified" button/modal; add an **Unassigned**
  option to the New-puzzle dropdown; un-disable the button when no metas exist.
- Accept: frontend build clean.

## Phase 4 — Fix the sheet reconcile (writes)
- `ensure_unassigned_table`: resolve tab by name via `get_page_name_by_id`; write the header
  row (id, Puzzle, Answer, Length, Notes); best-effort native Table for styling/hidden id.
- `apply_unassigned_changes`: fix the off-by-one grid indices (data row at A-col index i is
  grid row i, header at 0). Updates write B–D only; appends write A–D; **never touch Notes (E)**.
  Prunes delete whole rows bottom-first.
- Accept: suite green; **live smoke on test site required** (cannot unit-test Sheets).

## Verification
- `timeout 180 pipenv run python -m pytest tests/ -q`
- `pipenv run ruff check` on changed files
- `cd frontend && npm run build`
- Manual: create/solve/retag bang + web; confirm channel category + sheet rows.

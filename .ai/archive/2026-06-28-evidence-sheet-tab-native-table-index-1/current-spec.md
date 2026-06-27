# Current Spec

## Goal

Make the hunt-wide **Evidence** sheet tab render and position like the other roll-ups:
1. Render as a native Google Sheets **Table** (not a flat `aggregate_to_tab` value list).
2. Pin the tab to **index 1** — immediately after "Unassigned Feeders" (design order:
   `Unassigned Feeders | Evidence | REFERENCE | ...`).

This finishes shipped Milestone-D (native Tables) + zone-ordering work that left the
Evidence tab behind. It is **not** the deferred M9b feature (grouped-by-target views).

## In scope

- New `hunt.evidence_table_id` column (migration `017`) + `HuntData` field.
- New cog method `GoogleSheets.ensure_evidence_table(page_id)` mirroring
  `ensure_unassigned_table`: pin tab to index 1, write header, `addTable`, hide `id` col,
  one-time clear of the old flat render. Returns the table id.
- Rewrite `PuzzleService.refresh_evidence` to: lazy-create tab + table (guarded by
  `evidence_table_id is None`), build rows with a hidden leading `id` key column, and
  write via `reconcile_table` instead of `aggregate_to_tab`.
- Update/add tests.

## Out of scope

- M9b grouped-by-target / per-suspect dossier views (separate, unstarted feature).
- Any change to `aggregate_to_tab` itself (still used by `add_metapuzzle_data` etc.).
- Adding a solver-owned column to Evidence (all 5 cols are bot-owned).

## Relevant files

- `bot/core/puzzle_service.py` — `refresh_evidence` (~1801), `resolve_puzzle_names` (95).
- `bot/cogs/puzzles_gsheet.py` — `ensure_unassigned_table` (890, template), `reconcile_table`
  (1025), `_values_clear` (805), `create_blank_tab` (427).
- `bot/store/hunt_data.py` — `HuntData` dataclass (`import_dict` auto-reads new attrs).
- `migrations/017_evidence_table.sql` (new, model on `010_unassigned.sql`).
- Tests: `tests/test_evidence_service.py` (rewrite `TestRefreshEvidence`),
  `tests/test_contribution_table_requests.py` (add `ensure_evidence_table` request tests).

## Required behaviour

- Evidence table columns: `["id", "Source Puzzle", "Note", "Target Puzzle", "Logged At"]`;
  `id` is the hidden key column (evidence item id — unique; source-puzzle name is not).
- `reconcile_table(page_id, desired_rows, data_col_count=5)` — no numeric cols.
- Tab pinned to index 1 on first ensure (mirrors Unassigned's index-0 pin).
- One-time clear of pre-existing flat A5 render before creating the Table (migration safety).

## Constraints

- Mirror the Unassigned Feeders precedent exactly where possible; minimal divergence.
- Run focused tests under `timeout`, compact mode.
- Migration number `017` — verify no collision before commit (parallel worktrees).

## Acceptance criteria

- `refresh_evidence` calls `ensure_evidence_table` once (guarded) and `reconcile_table`
  with a 5-col desired set whose first column is the evidence id.
- `ensure_evidence_table` emits: a values clear, header write, `addTable` (name "Evidence",
  cols 0..5), a hidden-`id`-column request, and an `updateSheetProperties` move to index 1.
- `hunt.evidence_table_id` defaults to `None`.
- Focused evidence + contribution-table-request tests pass; ruff clean.

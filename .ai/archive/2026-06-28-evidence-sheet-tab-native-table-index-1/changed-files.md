# Changed Files

## Git status

Branch `working-1`, base was `6e86f84`; local `main` is `808bae6` (1 unrelated UI-fix
commit ahead — clean future merge, no migration collision; main tops out at migration 016).

Three feature commits:
- `fbd3bbb` Phase 1: hunt.evidence_table_id field + migration 017
- `e89c796` Phase 2: ensure_evidence_table (native Table, pinned after Unassigned Feeders)
- `72b8e3b` Phase 3: refresh_evidence renders via reconcile_table

## Files changed

- `migrations/017_evidence_table.sql` (new) — `ADD COLUMN evidence_table_id INT(11) NULL`.
- `bot/store/hunt_data.py` — new `evidence_table_id: Optional[int] = None` (auto read/write).
- `bot/cogs/puzzles_gsheet.py` — new `EVIDENCE_HEADER`, new `ensure_evidence_table(page_id)`.
- `bot/core/puzzle_service.py` — `refresh_evidence` rewritten to mirror `refresh_unassigned`.
- `tests/test_unassigned_service.py` — `evidence_table_id` default assertion.
- `tests/test_contribution_table_requests.py` — 3 `ensure_evidence_table` request tests.
- `tests/test_evidence_service.py` — `TestRefreshEvidence` flipped to the Table contract.

## Commands run

- `pipenv run python -m pytest tests/ -q` → 619 passed.
- `pipenv run ruff check <changed files>` → clean.
- Migration collision check across main + worktrees → 017 unique.

## Tests run

Full suite (619 passed). Focused: evidence service, contribution-table requests, unassigned.

## Tests not run

No live Google Sheets test (no live creds here). Mocked tests verify request shapes only;
runtime semantics covered by adversarial review (in progress) + faithful mirror of the
shipped Unassigned Feeders precedent.

## Current risks

- Live-migration first render on an existing flat Evidence tab (page_id set, table_id NULL):
  ensure_evidence_table clears old A5 data, pins to index 1, creates the Table at A1. Mirrors
  the proven unassigned path; verify on the test site after deploy.
- Tab-order is best-effort/cosmetic (matches existing code stance).

## Migration

`migrate.py` must apply `017_evidence_table.sql` before the new refresh_evidence runs live
(the dynamic UPDATE writes the evidence_table_id column).

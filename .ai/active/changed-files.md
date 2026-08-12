# Changed Files

## Git status

Branch `staging`, working tree clean. Plan 1 = 19 commits, `97d2d75..d280b93`.
`main` is fully contained in `staging` (no divergence to reconcile).

## Files changed (Plan 1)

Production:
- `migrations/043_feeder_pool.sql` (new) — `feeder_pool_items`, `feeder_claims`, FK cascades
- `bot/store/feeder_pool_data.py` (new) — `FeederPoolItemData`, `FeederClaimData`
- `bot/store/mysqldb.py` — `MySQLFeederPoolDb`, `MySQLFeederClaimDb`, `dedup_key`, `MAX_FEEDER_TEXT`
- `bot/store/__init__.py` — re-exports + `FeederPoolDb` / `FeederClaimDb` singletons
- `bot/core/feeder_pool.py` (new) — **PURE, import-free**: `claim_state`, `rival_names`,
  `provided_item_ids`, `diff_ticks`
- `bot/core/puzzle_service.py` — pool CRUD, claims, `get_claim`, `confirm_feeder_claims`,
  pool teardown in `remove_minimeta`; hook calls in `solve()` / `unsolve()`
- `bot/cogs/puzzles.py` — `_feeder_context`, `_build_feeder_pool_embed`, six commands
- `docs/commands.md` — feeder pool table + permissions rows

Tests (all new unless noted):
- `tests/test_feeder_pool_cascade.py` — **live DB**; FK cascades, unique constraint,
  collation dedup, all-or-nothing paste validation
- `tests/test_feeder_pool_pure.py` — the pure module
- `tests/test_feeder_pool_service.py` — service, DAOs patched at the service import site
- `tests/test_feeder_pool_confirm.py` — confirm-on-solve through the shared seam
- `tests/test_feeder_pool_commands.py` — cog behaviour, incl. mini-thread claim targeting
- `tests/test_command_gating.py` (modified) — six new tier entries

## Commands run

    timeout 400 pipenv run python -m pytest tests/ -q > /tmp/suite.log 2>&1
    timeout 60  pipenv run ruff check
    timeout 240 pipenv run basedpyright bot/
    timeout 120 pipenv run python migrate.py migrate

## Tests run

Full suite: **1981 passed**, 0 failed. ruff clean. `basedpyright bot/` 0 errors.
Migration 043 applied to `puzzlebot_test`.

## Tests NOT run

- Nothing exercises real Google Sheets (Plan 2 territory; the API is mocked throughout).
- No manual/live Discord test of the six new commands has been done. Worth one pass on a
  real hunt before trusting the embed rendering and the confirm-on-solve message.

## Current risks (Plan 1)

- `provided_item_ids` and `diff_ticks` are written and tested but **called by nothing** until
  Plan 2. Dead code until then, deliberately.
- The `!feeders` embed truncates via `split_lines_for_embed`; verified by test, never seen on
  a real Discord client.
- Confirm-on-solve fires on every mini solve, including minimetas with no pool (one zero-row
  UPDATE). Harmless, but it is a divergence from the spec's "non-empty pool" wording.

## Plan 2, Tasks 1-5 (this session)

Commits: `1495f0e` (probe, Task 1) `677014a` `584b4d0` `4e2e0d0` (Tasks 2-3, prior session)
`685687c` (Task 4) `135f89a` (Task 5).

Production, Task 4:
- `bot/core/sheet_tables.py` — `feeder_matrix_table_name(page_id)`
- `bot/cogs/puzzles_gsheet.py` — `ensure_feeder_matrix_tab` (idempotent on
  `feeder_tab_page_id`; addTable spans every column, BOOLEAN-typed tick columns)

Production, Task 5:
- `bot/cogs/puzzles_gsheet.py` — `render_feeder_matrix` (reconcile bot columns, rewrite
  header, widen the Table's span for a new mini, write TRUE for confirmed claims)
- `bot/core/puzzle_service.py` — `refresh_feeder_matrix` (ensure + render + badge the
  shared per-puzzle rollup row; `rollup_lock(("minimeta", id))`, the SAME key
  `refresh_minimeta` uses — never nest the two)

Tests (all new):
- `tests/test_feeder_matrix_requests.py` — cog-level request-shape assertions for both
  tasks (honesty header: asserts shape against mocks, not live Sheets behaviour)
- `tests/test_feeder_matrix_service.py` — `refresh_feeder_matrix` guards/persistence/badge,
  gsheet cog mocked

## Commands run (Tasks 4-5)

    timeout 400 pipenv run python -m pytest tests/ -q > /tmp/suite.log 2>&1
    timeout 60  pipenv run ruff check
    timeout 240 pipenv run basedpyright bot/

## Tests run (Tasks 4-5)

Full suite: **2014 passed** (1996 baseline + 18 new), 0 failed. ruff clean.
`basedpyright bot/` 0 errors. `grep -nE "^(from|import) " bot/core/feeder_pool.py` empty
(untouched by these tasks). Three mutation checks against the new cog code (BOOLEAN typing,
boolValue tick write, table-widen guard) and one against the service's empty-pool guard all
went red on exactly the targeted test, then were restored.

## Tests NOT run (Tasks 4-5)

- No manual pass against a real Google Sheet with these two functions specifically — only
  Task 1's probe (a standalone script) touched live Sheets. `!sync_claims` (Task 6) is what
  would let a full create→tick→sync loop be exercised manually.
- `refresh_feeder_matrix` is not yet called from anywhere (Task 8 wires the lifecycle) —
  dead code until then, deliberately, same pattern as Plan 1's `provided_item_ids`.

## Current risks (Tasks 4-5)

- `ensure_feeder_matrix_tab`'s idempotency is keyed on the TAB existing, not the native
  Table. If `addTable` fails transiently on first creation, the tab is left without a Table
  and is never retried (the same tradeoff `ensure_unassigned_table`'s caller already
  accepts) — a solver would see a plain tab with no checkboxes until an organiser notices.
  No self-heal-on-render path (like `_ensure_contribution_table`) was built for the matrix;
  out of scope for Tasks 4-5 as specified.
- `render_feeder_matrix`'s confirmed-tick write does a second `_values_get` after
  `reconcile_table` settles, to find each item's row. Correct, but not specified verbatim by
  the plan (which only sketched the `reconcile_table` call) — flagged in the task report as
  the one place judgment was needed rather than a literal instruction.

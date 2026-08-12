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

## Current risks

- `provided_item_ids` and `diff_ticks` are written and tested but **called by nothing** until
  Plan 2. Dead code until then, deliberately.
- The `!feeders` embed truncates via `split_lines_for_embed`; verified by test, never seen on
  a real Discord client.
- Confirm-on-solve fires on every mini solve, including minimetas with no pool (one zero-row
  UPDATE). Harmless, but it is a divergence from the spec's "non-empty pool" wording.

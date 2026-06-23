# Changed Files

## Git status

Branch `feature/m8-minimeta-fixes` @ dc4805e (off local main). No code changes yet.
Only new files: `.ai/active/*` (spec, plan, session-summary, this file).

## Files changed

- (none yet — investigation + planning only)

## Planned files (see implementation-plan.md)

- `bot/core/puzzle_service.py` (Phases 1, 2, 3)
- `bot/cogs/puzzles.py` (Phase 2: list_minis render + refresh triggers)
- `bot/cogs/puzzles_gsheet.py` (Phase 1 sheet url helper reuse; Phase 3 reuses
  add_metapuzzle_data — likely no change)
- `bot/store/rollup_data.py` + `migrations/008_rollup_puzzle_target.sql` (Phase 3)
- `bot/store/mysqldb.py` (Phase 3 rollup persistence if needed)
- `tests/test_minimeta_service.py`, `tests/test_minimeta_commands.py`,
  `tests/test_refresh_minimeta.py` (new), `tests/test_rollup_data.py`
- `frontend/dist` (Phase 4, gitignored — rebuild artifact)

## Commands run (investigation)

- git topology checks (working-2 is ancestor of main; M8 lives on main dc4805e).
- `git worktree add -b feature/m8-minimeta-fixes .claude/worktrees/m8-fixes main`.
- Read create_mini, list_minis, refresh_meta, select_rollup_members, RollupData,
  rollups schema (002), data.py, _dist_dir.

## Tests run

- `pipenv run python -m pytest tests/test_minimeta_service.py` → FAILED to run:
  (a) fresh worktree venv has no pytest (`pipenv install --dev` / sync needed);
  (b) no `config.json` in this worktree (DB creds; store opens MySQL at import).

## Tests not run

- All — harness not yet runnable in this worktree (see above).

## ENV SETUP NEEDED before TDD

1. Provide `config.json` (copy from main checkout `/home/russ/projects/puzzbot/` or a
   sibling worktree — gitignored; points at local `puzzlebot_test`).
2. Install deps in this worktree's venv: `pipenv install --dev` (or `pipenv sync`).
3. Confirm `puzzlebot_test` DB reachable, then run the suite.

## Current risks

- Phase 3 migration must not break the round-keyed rollup path (relaxing
  round_id NOT NULL / uq_rollups_round). Verify no query assumes NOT NULL.
- Possible parallel session in worktree-working-3 (dirty puzzles.py) — avoid collisions
  when this lands on main.

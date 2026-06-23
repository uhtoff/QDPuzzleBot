# Current Spec

## Goal

Fix four defects in the M8 "mini-meta" feature (already shipped on `main`, partially
working). Threads are created, minis can be solved, and `!list_minis` reflects solved
state — but four things are broken/missing.

## In scope (the four fixes)

1. **Thread welcome message** — when a mini is created, its Discord thread must post a
   welcome message: "This thread is for mini puzzle <name> from mini meta {{minimeta}}"
   with a link to the mini's Google Sheet tab.
2. **`!list_minis` into the pinned info** — the mini list must be written into the
   minimeta channel's pinned info message AND refreshed on change (add/solve/unsolve),
   mirroring how `add_sheet` adds "Additional Sheet Links" to the pin via
   `info(update=True)`. Today `!list_minis` only fires a throwaway embed.
3. **Feeders (minis) onto the minimeta's own sheet** — minis must roll up (name +
   answer) onto the **minimeta puzzle's own Google sheet**, via the **rollup engine**.
   Today the rollup engine is round-keyed only, so minis never roll up onto the
   minimeta. DECISION (user): reuse the rollup engine and generalise it to be
   **puzzle-keyed** ("rollup maintains tables" direction), not via a fake round.
4. **Dashboard shows minis** — backend already serves `mini_puzzles`; the cause is a
   **stale/missing `frontend/dist`** build (gitignored). Fix = rebuild the SPA and
   redeploy. Not a code bug.

## Out of scope

- Reworking normal round-based metas.
- Any change to working-2 / Phase B board-push work.
- Hunt-wide overview/index sheet (confirmed none exists; user wants the minimeta's own
  sheet).

## Relevant files (worktree: .claude/worktrees/m8-fixes, branch feature/m8-minimeta-fixes)

- `bot/core/puzzle_service.py` — `create_mini` (~428), `make_minimeta` (405),
  `get_minis` (411), `refresh_meta` (47), `select_rollup_members` (85),
  `_render_rollup` (109), `_find_or_create_rollup` (76), `solve` (rollup trigger).
- `bot/cogs/puzzles.py` — `list_minis` (930), `add_mini` (911), `solve_mini` (948),
  `_build_embed_list`, `send_initial_*_channel_messages`, `info`, `web_refresh_info`.
- `bot/cogs/puzzles_gsheet.py` — `add_metapuzzle_data(puzzle, members)` (686) writes a
  rollup onto ANY puzzle's sheet; `create_puzzle_spreadsheet` (511); `urls`/sheet url.
- `bot/store/rollup_data.py` — `RollupData` (round-keyed today).
- `bot/store/mysqldb.py` — `get_children` (309), `get_all_from_round` (298), rollup
  persistence.
- `migrations/` — last is 006; rollups table is `002_rollups.sql` (round_id NOT NULL,
  uq_rollups_round, FK to rounds). Need `008_*` (007 is the M8 parent_puzzle_id one
  already on main).
- `bot/web/__init__.py:41` `_dist_dir()` — serves SPA from `frontend/dist`.
- `frontend/src/components/MiniPuzzlePanel.tsx`, `frontend/src/types.ts`,
  `frontend/src/api.ts`, `bot/web/data.py` (mini_puzzles payload — already done).

## Required behaviour

See the four In-scope items. For #3, on solving/unsolving a mini the minimeta's sheet
must re-render its minis table; the existing `add_metapuzzle_data(minimeta, minis)`
writer is reused, members = `get_children(minimeta.id)`.

## Constraints

- TDD. Test command:
  `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
  (needs reachable `puzzlebot_test` DB via gitignored config.json).
- Small, reviewable, per-stage commits. Don't touch unrelated dirty files.
- Migrations are additive/backward-compatible (follow the 002/007 style).
- Caution: `worktree-working-3` had an uncommitted edit to `bot/cogs/puzzles.py` —
  possible parallel session; do not touch that worktree.

## Acceptance criteria

- [ ] Creating a mini posts the welcome message + sheet link in the thread.
- [ ] `!list_minis` content appears in the minimeta's pinned info and refreshes on
      add/solve/unsolve.
- [ ] Solving a mini renders the minis (name+answer) onto the minimeta's own sheet via
      the rollup engine (puzzle-keyed rollup).
- [ ] Dashboard shows minis after SPA rebuild (verified locally; deploy is the user's).
- [ ] Focused tests pass; no regressions in existing minimeta/rollup tests.

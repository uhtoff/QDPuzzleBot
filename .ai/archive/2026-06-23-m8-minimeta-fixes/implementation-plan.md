# Implementation Plan

Order chosen to ship the clear, low-risk fixes first and isolate the big refactor (#3).
Each stage: write failing test(s) → implement → run focused tests → review diff → commit.

## Phase 1 — Thread welcome message (#1)  [small, independent]

- In `PuzzleService.create_mini`, after the thread is created and `mini.google_page_id`
  is known, post a welcome message into the thread:
  `f"This thread is for mini puzzle **{mini.name}** from mini meta **{minimeta.name}**."`
  plus a markdown link to the mini's sheet tab
  (`urls.spreadsheet_url(spreadsheet_id, mini.google_page_id)` — confirm helper +
  how to get the hunt spreadsheet id, mirror `create_additional_sheet_list`).
- Keep Discord side-effects best-effort (don't fail the create on a Discord hiccup).
- Test: extend `tests/test_minimeta_service.py` — assert `thread.send` called with the
  welcome text + sheet url (mock thread). Verify ordering (page id set before send).

## Phase 2 — `!list_minis` into the pinned info (#2)

- Add a renderer mirroring `create_additional_sheet_list`, e.g.
  `create_mini_list(puzzle)` → numbered ✅/⬜ lines (reuse list_minis formatting).
- In `_build_embed_list` (or the minimeta info renderer
  `send_initial_metapuzzle_channel_messages` / puzzle one), append a "Mini-puzzles"
  embed/field when `service.get_minis(puzzle.id)` is non-empty.
- Trigger refresh (`info(ctx, update=True)` / `web_refresh_info`) after `add_mini`,
  `solve_mini`, `unsolve_mini` — same pattern as `add_sheet`.
- Decide: is a minimeta rendered via `send_initial_metapuzzle_channel_messages` or the
  plain puzzle path? `make_minimeta` only sets `puzzle_type='Minimeta'` (NOT
  `metapuzzle=1`), so it currently uses the plain puzzle renderer — wire the mini list
  there, gated on `puzzle.puzzle_type == 'Minimeta'` (or `get_minis` non-empty).
- Tests: `tests/test_minimeta_commands.py` — assert refresh called after add/solve;
  assert mini list rendered in the embed list.

## Phase 3 — Puzzle-keyed rollup onto the minimeta sheet (#3)  [the refactor]

Generalise the rollup engine from round-keyed to also support a puzzle (minimeta) target.

- **Migration `008_rollup_puzzle_target.sql`**: add `target_puzzle_id BIGINT NULL` to
  `rollups`; make `round_id` nullable; relax `uq_rollups_round` to allow puzzle-keyed
  rows (e.g. add `uq_rollups_target_puzzle` and drop the NOT NULL). Additive, backward
  compatible — existing round rows keep `round_id`, `target_puzzle_id NULL`.
- **`RollupData`**: add `target_puzzle_id: Optional[int] = None`.
- **Service**: add a puzzle-keyed path. Cleanest shape:
  - `refresh_minimeta(hunt, minimeta)` → `rollup_lock` on a puzzle key;
    `_find_or_create_rollup_for_puzzle(minimeta)` (get_by_attr target_puzzle_id);
    members = `get_minis(minimeta.id)` (children); render via existing
    `add_metapuzzle_data(minimeta, members)`; `_record_rollup_tab`; `_mark_rollup`.
  - Factor the shared body of `refresh_meta` so both round + puzzle paths reuse
    render/record/mark (keep diff focused; don't rewrite round path behaviour).
- **Trigger**: in `solve` / `unsolve`, when the puzzle has `parent_puzzle_id`, call
  `refresh_minimeta(hunt, parent)` (look up parent via `get_by_attr(id=parent_id)`).
  Remove/!revisit the create_mini "tag into minimeta.tags[0]" hack so minis don't also
  leak onto the round's real meta (verify against test_meta_isolation expectations).
- Tests: new `tests/test_refresh_minimeta.py` (mirror `test_refresh_meta.py`) — solving
  a mini renders minis onto the minimeta sheet; unsolve removes the answer; round metas
  unaffected (no leak). Extend `test_rollup_data.py` for the new field.

## Phase 4 — Dashboard SPA rebuild (#4)  [deploy, not code]

- `cd frontend && npm install && npm run build` (`tsc --noEmit && vite build`) →
  produces `frontend/dist`. Confirm the bundle references the mini panel.
- Verify locally that the dashboard renders `mini_puzzles` (panel under a parent).
- Deploy to redhat:8099 is the user's step (test-site topology in AGENTS.md). Document
  the rebuild command; do not attempt remote deploy.

## Verification

- Focused per-phase tests, then a full run:
  `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`.
- Ruff lint on touched files.
- Manual: bot run for welcome msg + pin + sheet rollup if a test guild is available.

## Rollback / recovery notes

- All on branch `feature/m8-minimeta-fixes` off `main` (dc4805e). Each phase is its own
  commit; revert per-commit if a phase regresses.
- Migration 008 is additive; down = drop column/relaxed keys (note in the .sql header).

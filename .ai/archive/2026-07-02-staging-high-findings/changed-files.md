# Changed Files

## Git status

Branch: `working-1` (based on `staging`, tip now `dfeafc4`). Worktree already had
`config.json` + `google_secrets.json` present (bootstrap step was a no-op check).

## Files changed

- `.ai/active/*.md` — handoff docs (created, then updated as each finding landed)
- `bot/cogs/puzzles.py` — `archive_channel`: archive on content OR attachments OR embeds,
  not content-only (#1)
- `tests/test_archive_channel.py` — new regression tests for attachment-only/embed-only
  archiving and the truly-empty-message skip
- `bot/cogs/puzzles_gsheet.py` — `reconcile_table`: prune blanks the table's own columns in
  place instead of `deleteDimension` on ROWS; appends land after the highest row in use,
  never reusing a blanked row (#3)
- `tests/test_contribution_table_requests.py` — updated
  `test_reconcile_table_appends_updates_prunes` to assert blank-in-place + stable append
  position instead of `deleteDimension`
- `bot/core/puzzle_service.py` — `convert_to_puzzle`: pop the deleted round from
  `puzzle.tag_edges`, not just `puzzle.tags` (#5)
- `tests/test_solve_equivalence.py` — new `test_removes_round_from_tag_edges`
- `bot/web/notifier.py` — `WebNotifier.board_changed`: resolve `hunt.guild_id` (the guilds
  row id) to the Discord snowflake via `data.get_guilds_map()` before publishing (#6)
- `tests/test_web_notifier.py` — rewrote the board-event test to mock `get_guilds_map` and
  assert the resolved snowflake (the old test asserted the buggy pass-through); added a
  case for an unknown guild row
- `frontend/src/components/AdminSettingsPanel.tsx` — UTC-offset `Select` for hunt start time
  + offset-arithmetic conversion (#7); replaced render-body derived-state sync with a
  `useEffect` keyed on `uid` so edits survive the dashboard's 30s background poll (#8)
- `frontend/src/util.ts` — extracted the #7 offset helpers (`browserOffsetMinutes`,
  `UTC_OFFSET_OPTIONS`, `tsToLocal`, `localToTs`) here so they're shared, not duplicated
- `frontend/src/pages/HuntList.tsx` — `NewHuntModal`: same UTC-offset picker, applied to
  the create-hunt start time (user follow-up request, not from the original review)
- `bot/cogs/puzzles_gsheet.py` — `restore_puzzle_spreadsheet`: gate the additional-sheets
  move-back on the puzzle's captured `was_archived` state, not the already-cleared
  `.solved` flag (#2)
- `bot/core/puzzle_service.py` — `unsolve`: commit AFTER `restore_puzzle_spreadsheet`
  mutates archived/google_page_id, not before (#4); `solve`/`change_solution`/
  `add_solution`: isolate their own Sheets-write failure the same way meta refreshes
  already are, so a failure doesn't skip the second commit/forum update/board push (#28)
- `tests/test_sheet_ordering.py`, `tests/test_minimeta_service.py` — regression tests for
  #2/#4
- `tests/test_sheet_failure_isolation.py` — new regression tests for #28
- `bot/web/actions.py` — `solve_puzzle`: post the mode-appropriate embed + refresh the pin
  for `partial`/`change`/`add` modes, not just `full` (#62)
- `bot/cogs/puzzles.py` — new `web_post_solution_update_embed` (#62); merged
  `change_solution`/`add_solution`'s duplicate bang-command tails into
  `_change_or_add_solution_impl` and added the Follow-up field `add_solution`'s embed was
  missing (#80); `_solve_impl`/`web_post_solved_embed` now use the shared
  `build_solved_embed` formula (#79)
- `bot/core/notifier.py` — new shared `build_solved_embed` helper (#79); removed the dead
  `puzzle_solved` interface method, its four implementations, and the `_post_solved_embed`
  fallback (found while working #79 — never called from outside this module; live push
  path is `board_changed`)
- `bot/web/notifier.py` — removed `WebNotifier.puzzle_solved` (dead, see above)
- `tests/test_web_solution_update_message.py` — new regression tests for #62
- `tests/test_change_add_solution_impl.py` — new regression tests for #79/#80
- `tests/test_notifier.py`, `tests/test_web_notifier.py`, `tests/test_board_push_service.py`
  — dropped assertions/fixture lines referencing the removed `puzzle_solved`

## Diff summary

- b0225d6 `fix(archive): preserve attachment/embed-only messages (#1)`
- 5df7fe5 `docs(ai): record #1 fix in handoff tracker`
- 3ed8ae1 `fix(gsheet): stop deleting sheet rows on contribution/unassigned prune (#3)`
- 112fc27 `docs(ai): record #3 fix and Q1 resolution in handoff tracker`
- 26e8e3f `fix(puzzle_service): clear tag_edges on convert_to_puzzle demote (#5)`
- 0da35ea `fix(web): publish the guild snowflake from board_changed, not the row id (#6)`
- 2e77ca5 `docs(ai): record Phase 2 (#5, #6) in handoff tracker`
- 1740bab `fix(web): hunt-start timezone picker + stop wiping in-progress edits (#7, #8)`
- acda3db `docs(ai): record Phase 3 (#7, #8) and Q3 resolution in handoff tracker`
- dfeafc4 `feat(web): add the same UTC-offset picker to the New Hunt modal`
- 3c4042a `fix(gsheet): unsolve archive-spreadsheet desync (#2, #4)`
- 3b5bb70 `docs(ai): record Phase 4 (#2, #4) landing in handoff tracker`
- 25154f0 `fix(web): post embed + refresh pin for partial/change/add solves (#62)`
- 550437d `docs(ai): record Phase 5 (#62) landing — all 9 HIGH findings complete`
- 5435deb `fix(service): isolate own-sheet failures in solve/change/add (#28)`
- 4ec4444 `refactor: dedupe the solved embed and change/add-solution tails (#79, #80)`
- ab4f37b `docs(ai): record folded-in #28, #79, #80 in handoff tracker`
- 32ef6fc `refactor: remove dead puzzle_solved notifier path`
- 1183eb8 `docs(ai): record dead puzzle_solved notifier path removal`

## Commands run

- `git branch -d fix-correctness-review` — deleted stale, fully-merged branch (was 973e5f0)
- `pipenv run python -m pytest --collect-only -q` — 747 tests collected, confirms bootstrap
- Per backend finding: focused pytest on the touched test file, `ruff check` on touched
  files, then full `pipenv run python -m pytest tests/ -q` — all green after each of
  #1, #3, #5, #6 (final backend count: 751 passed)
- #5: sanity-checked the new test is a real regression test by stashing the source fix,
  rerunning `-k test_removes_round_from_tag_edges` (failed as expected), then restoring
- #7/#8 (frontend, no test runner in this repo): `npx tsc --noEmit` — clean;
  `npm run build` (tsc + vite build) — clean; standalone Node script round-tripping the
  UTC-offset conversion at +120/-300/0 minutes — exact
- #2, #4, #28, #79/#80: each new regression test verified red against the pre-fix source
  via `git stash push -- <file>` / rerun / confirm failure / `git stash pop`, matching the
  pattern established for #1/#3/#5
- `ruff check .` (repo-wide) run clean after every backend finding from #2 onward

## Tests run

Backend: full suite green throughout, ending at 766 passed (final count after #28/#79/#80
and the dead-code removal — the removal deleted its own coverage 1:1, no net change).
Frontend: no test runner exists in this repo; verified via typecheck + build + a manual
math round-trip script (see above). Not independently browser-tested — no live Quart
backend/auth available in this worktree to exercise the actual dashboard page.

## Tests not run

None remaining in scope. All 9 HIGH findings (#1,#3,#5,#6,#7,#8,#2,#4,#62) plus the
folded-in #28/#79/#80 are implemented and tested.

## Current risks

- #3's new blank-in-place/append-after-max behaviour is unit-tested against the mocked
  Sheets seam only; not yet verified against a live scratch sheet with real Table semantics
  (per the plan's rollback notes, prefer proving destructive-path fixes on a scratch
  hunt/sheet before trusting them on real data).
- #7/#8 have not been exercised in a real browser against a live hunt — worth a manual
  click-through (save with a non-UTC offset, confirm the round-trip; edit a field, wait for
  a 30s poll, confirm it isn't wiped) before this ships.
- Several findings were dual-path (bang + web); each was checked for both entrypoints
  where applicable.
- Task complete: not independently browser-tested end-to-end against a live hunt (same
  constraint noted for #7/#8 above — no live Quart backend/auth in this worktree).

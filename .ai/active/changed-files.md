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

## Tests run

Backend: full suite green (751 passed) as of #6.
Frontend: no test runner exists in this repo; verified via typecheck + build + a manual
math round-trip script (see above). Not independently browser-tested — no live Quart
backend/auth available in this worktree to exercise the actual dashboard page.

## Tests not run

Remaining findings (#2, #4, #62) not yet touched.

## Current risks

- #3's new blank-in-place/append-after-max behaviour is unit-tested against the mocked
  Sheets seam only; not yet verified against a live scratch sheet with real Table semantics
  (per the plan's rollback notes, prefer proving destructive-path fixes on a scratch
  hunt/sheet before trusting them on real data).
- #7/#8 have not been exercised in a real browser against a live hunt — worth a manual
  click-through (save with a non-UTC offset, confirm the round-trip; edit a field, wait for
  a 30s poll, confirm it isn't wiped) before this ships.
- Several remaining findings are dual-path (bang + web); fixing one path can leave the
  other stale.

# Changed Files

## Git status

Branch: `working-1` (based on `staging`, tip now `0da35ea`). Worktree already had
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

## Diff summary

- b0225d6 `fix(archive): preserve attachment/embed-only messages (#1)`
- 5df7fe5 `docs(ai): record #1 fix in handoff tracker`
- 3ed8ae1 `fix(gsheet): stop deleting sheet rows on contribution/unassigned prune (#3)`
- 112fc27 `docs(ai): record #3 fix and Q1 resolution in handoff tracker`
- 26e8e3f `fix(puzzle_service): clear tag_edges on convert_to_puzzle demote (#5)`
- 0da35ea `fix(web): publish the guild snowflake from board_changed, not the row id (#6)`

## Commands run

- `git branch -d fix-correctness-review` — deleted stale, fully-merged branch (was 973e5f0)
- `pipenv run python -m pytest --collect-only -q` — 747 tests collected, confirms bootstrap
- Per-finding: focused pytest on the touched test file, `ruff check` on touched files, then
  full `pipenv run python -m pytest tests/ -q` — all green after each of #1, #3, #5, #6
- #5: sanity-checked the new test is a real regression test by stashing the source fix,
  rerunning `-k test_removes_round_from_tag_edges` (failed as expected), then restoring
- Final full suite: `pipenv run python -m pytest tests/ -q` — 751 passed

## Tests run

Full suite green (751 passed) after #6, the latest landed finding.

## Tests not run

Remaining findings (#7, #8, #2, #4, #62) not yet touched.

## Current risks

- #3's new blank-in-place/append-after-max behaviour is unit-tested against the mocked
  Sheets seam only; not yet verified against a live scratch sheet with real Table semantics
  (per the plan's rollback notes, prefer proving destructive-path fixes on a scratch
  hunt/sheet before trusting them on real data).
- Several remaining findings are dual-path (bang + web); fixing one path can leave the
  other stale.

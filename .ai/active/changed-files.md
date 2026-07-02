# Changed Files

## Git status

Branch: `working-1` (based on `staging`, tip now `b0225d6`). Worktree already had
`config.json` + `google_secrets.json` present (bootstrap step was a no-op check).

## Files changed

- `.ai/active/*.md` — handoff docs (created, then updated as Phase 1 landed)
- `bot/cogs/puzzles.py` — `archive_channel`: archive on content OR attachments OR embeds,
  not content-only (#1)
- `tests/test_archive_channel.py` — new regression tests for attachment-only/embed-only
  archiving and the truly-empty-message skip

## Diff summary

- b0225d6 `fix(archive): preserve attachment/embed-only messages (#1)` — 2 files changed,
  141 insertions, 27 deletions

## Commands run

- `git branch -d fix-correctness-review` — deleted stale, fully-merged branch (was 973e5f0)
- `pipenv run python -m pytest --collect-only -q` — 747 tests collected, confirms bootstrap
- `pipenv run python -m pytest tests/test_archive_channel.py -q` — 2 passed
- `pipenv run python -m pytest tests/ -q -k "archive or puzzles"` — 34 passed
- `pipenv run ruff check bot/cogs/puzzles.py tests/test_archive_channel.py` — clean
- `pipenv run python -m pytest tests/ -q` — 749 passed (full suite)

## Tests run

Full suite green (749 passed) after the #1 fix.

## Tests not run

None outstanding for #1. Remaining findings (#3, #5, #6, #7, #8, #2, #4, #62) not yet
touched.

## Current risks

- #3 (reconcile_table) still touches a destructive path (deleteDimension ROWS); validate
  the fix against real Sheets range semantics, ideally on a scratch sheet, before trusting
  it on real data.
- Several remaining findings are dual-path (bang + web); fixing one path can leave the
  other stale.

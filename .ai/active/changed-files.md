# Changed Files

## Git status

Branch: `working-1` (based on `staging`, tip now `3ed8ae1`). Worktree already had
`config.json` + `google_secrets.json` present (bootstrap step was a no-op check).

## Files changed

- `.ai/active/*.md` — handoff docs (created, then updated as each Phase 1 finding landed)
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

## Diff summary

- b0225d6 `fix(archive): preserve attachment/embed-only messages (#1)` — 2 files changed,
  141 insertions, 27 deletions
- 5df7fe5 `docs(ai): record #1 fix in handoff tracker` — handoff docs only
- 3ed8ae1 `fix(gsheet): stop deleting sheet rows on contribution/unassigned prune (#3)` —
  2 files changed, 47 insertions, 17 deletions

## Commands run

- `git branch -d fix-correctness-review` — deleted stale, fully-merged branch (was 973e5f0)
- `pipenv run python -m pytest --collect-only -q` — 747 tests collected, confirms bootstrap
- `pipenv run python -m pytest tests/test_archive_channel.py -q` — 2 passed
- `pipenv run python -m pytest tests/ -q -k "archive or puzzles"` — 34 passed
- `pipenv run ruff check bot/cogs/puzzles.py tests/test_archive_channel.py` — clean
- `pipenv run python -m pytest tests/ -q` — 749 passed (after #1)
- `pipenv run python -m pytest tests/test_contribution_table_requests.py -q` — 36 passed
  (after updating the prune assertion for #3)
- `pipenv run ruff check bot/cogs/puzzles_gsheet.py tests/test_contribution_table_requests.py`
  — clean
- `pipenv run python -m pytest tests/ -q` — 749 passed (after #3)

## Tests run

Full suite green (749 passed) after both #1 and #3.

## Tests not run

Remaining findings (#5, #6, #7, #8, #2, #4, #62) not yet touched.

## Current risks

- #3's new blank-in-place/append-after-max behaviour is unit-tested against the mocked
  Sheets seam only; not yet verified against a live scratch sheet with real Table semantics
  (per the plan's rollback notes, prefer proving destructive-path fixes on a scratch
  hunt/sheet before trusting them on real data).
- Several remaining findings are dual-path (bang + web); fixing one path can leave the
  other stale.

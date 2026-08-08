# Changed Files

## Git status

Worktree: `/home/russ/projects/puzzbot/.claude/worktrees/working-2`, branch `working-2`.

Clean apart from untracked `node_modules/` (pre-existing, left alone) and these
`.ai/active/` handoff files.

```
658ff54 docs: mark H5 (convert/delete deadlock) resolved
cd05c68 fix(cogs): confirm destructive bang commands BEFORE taking the puzzle locks (H5 aggravator)
0173802 fix(locks): one sorted acquisition per group mutation, killing the convert/delete deadlock (H5)
f3df818 fix(db): FK hunt_category -> hunts with ON DELETE CASCADE (T3)   <- staging base
```

Nothing merged to `staging`; nothing pushed to `origin`.

## Files changed

`git diff --stat f3df818..HEAD` — 11 files, +591 / −89.

### Production

| File | Why |
|---|---|
| `bot/core/locks.py` | New `convert_lock_ids()`. Module docstring now leads with the "ONE SORTED ACQUISITION PER MUTATION (H5)" rule; the `bulk_re_edge` entry removed from the deliberate-exceptions list (three → two); `puzzles_lock` docstring warns that sorted order only prevents cycles while it is the *only* acquisition a task makes. |
| `bot/core/puzzle_service.py` | `bulk_re_edge` no longer acquires member locks — the `AsyncExitStack`/`puzzle_lock` block is gone and the comment now states the caller holds them. Dropped the then-unused `AsyncExitStack` import. |
| `bot/web/actions.py` | All three convert routes take `puzzles_lock(convert_lock_ids(...))`: `convert_to_meta` (~:712), `_convert_to_type_handler` (~:1341), `change_group_type` (~:1689, replacing the target-only `AsyncExitStack`). Dropped the then-unused `AsyncExitStack` import. |
| `bot/cogs/puzzles.py` | `_run_convert_to_type` now takes the same lock set and re-reads the puzzle under it (it had no lock at all). `archive_hunt`, `delete_round`, `delete_hunt`: `_confirm_destructive` moved **outside** `puzzles_lock`, member ids re-read after the click. |

### Tests

| File | Why |
|---|---|
| `tests/test_lock_ordering.py` | **New**, 12 tests. The H5 regression cover. |
| `tests/test_edge_primitives.py` | `test_flip_waits_for_in_flight_member_command` → `test_flip_takes_no_lock_of_its_own` (inverted contract, guarantee relocated to the caller). |
| `tests/test_change_group_type_web.py` | `test_change_type_does_not_deadlock_when_service_locks_a_feeder` → `test_change_type_locks_target_and_members_in_one_acquisition` (asserts target AND members held, against real locks). |
| `tests/test_archive_hunt_command.py`, `tests/test_group_type_consolidation.py` | Docstrings only — they claimed the lock was held "for the duration of confirm+delete/archive", which stopped being true. Assertions unchanged and still pass. |

### Docs

| File | Why |
|---|---|
| `docs/reviews/2026-08-07-comprehensive-review.md` | H5 marked **RESOLVED** with commit hashes; the review's claim that the bang converts entered the cycle via `with_puzzle_mutex` corrected; "Still open" list and "Suggested order of attack" item 5 updated. |
| `docs/superpowers/plans/2026-08-08-atomicity.md` | Header pointer "H5 … remains open and is the next item" replaced with the fix reference. |

## Commands run

```bash
timeout 400 pipenv run python -m pytest tests/ -q > <log> 2>&1
timeout 180 pipenv run python -m pytest tests/test_lock_ordering.py -q > <log> 2>&1
timeout 60  pipenv run ruff check
timeout 300 pipenv run basedpyright bot/
```

## Tests run

- Full suite on the final tree: **1700 passed**, 28 warnings, ~12s.
- Full suite re-run at the stage-1 commit boundary: 1697 passed (the three
  confirm-before-lock tests were not yet added).
- `ruff check`: All checks passed. `basedpyright bot/`: 0 errors, 0 warnings.

### Falsification evidence (tests written red first)

All 12 new tests failed against the pre-fix code, each for the right reason:

- `test_bulk_re_edge_does_not_acquire_member_locks` and
  `test_bulk_re_edge_still_serializes_via_the_caller_held_lock` → **`TimeoutError`**,
  i.e. the literal deadlock.
- `test_group_keyed_convert_route_holds_target_and_members` → `{5050} != {5048, 5050}`
  (web route held only the target).
- `test_puzzle_keyed_convert_route_holds_target_and_members` → `{5150} != {5148, 5150}`.
- `test_bang_convert_holds_target_and_members` → `set() != {5248, 5250}` (bang held
  nothing at all).
- The three confirm tests → `['lock', 'confirm'] != ['confirm', 'lock']`.
- The four `TestConvertLockIds` cases → `ImportError` (helper did not exist).

## Tests not run

- No runtime/production reproduction of the deadlock against a live bot — the fix and
  its cover are structural, same as the review's own analysis.
- No DB was connected beyond the import-time `puzzlebot_test` connection the suite
  needs; this branch adds no migration.
- Frontend (`npx tsc --noEmit`) not run — no frontend files touched.

## Current risks

- **Low.** The change removes lock acquisitions rather than adding them, so the failure
  mode of a mistake here is a lost update, not a new deadlock. The two windows that
  remain are documented on `convert_lock_ids` (member set read just before the lock;
  `convert_tag_group_type`'s target created inside the section) and both pre-date this
  work.
- Not yet merged to `staging` and not exercised on the test site.

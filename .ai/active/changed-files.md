# Changed Files

## Git status

```
 M bot/cogs/puzzles.py
?? docs/reviews/2026-07-02-staging-full-review.md
```

## Files changed

- `bot/cogs/puzzles.py` — tranche 2 fixes for findings #17 and #74 from
  docs/reviews/2026-07-02-staging-full-review.md.

## Diff summary

4 hunks in bot/cogs/puzzles.py, pure statement reordering (no line count change):
- `feeds_only`, `remove_from`, `informs_only`, `groups_only_with`: moved the ✅ ctx.send
  ack to run AFTER the service mutation (+ refresh_unassigned_safe task) instead of before,
  matching the sibling commands (`feeds`, `informs`, `groups_with`) which already ack after
  the mutation succeeds. Previously a Sheets/Discord failure in the mutation left the user
  with a false success confirmation.

## Commands run

```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-tranche2.log 2>&1
```

## Tests run

766 passed, 0 failed (full suite), both after tranche 1 and again after tranche 2.

## Tests not run

None relevant — full suite covers this file.

## Current risks

None new. Not yet committed.

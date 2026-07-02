# Changed Files

## Git status

```
 M bot/cogs/puzzles_gsheet.py
?? docs/reviews/2026-07-02-staging-full-review.md
```

## Files changed

Tranche 4 fixes for findings #25, #26, #47, #48 from
docs/reviews/2026-07-02-staging-full-review.md (#24 explicitly deferred — see
session-summary.md).

- `bot/cogs/puzzles_gsheet.py`:
  - New module-level `_formula_string()` helper; applied at both HYPERLINK-building sites
    (`update_puzzle_info`, `_puzzle_tab_header_requests`) to escape embedded `"` in puzzle
    names/urls.
  - `update_puzzle_info`'s tab-rename uniqueness check now targets the correct spreadsheet
    (archive vs live) based on `puzzle.archived`.
  - `update_cell`'s dead `self.get_puzzle_data()` fallback replaced with an explicit
    `ValueError`.
  - `move_sheet_to_end`'s off-by-one (`len(sheets) - 1` → `len(sheets)`) fixed.

## Commands run

```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-tranche4.log 2>&1
pipenv run ruff check bot/cogs/puzzles_gsheet.py
```

## Tests run

766 passed, 0 failed (full pytest suite). `ruff check` clean.

## Tests not run

No test exercises `move_sheet_to_end` directly (grep confirms zero test call sites) — the
off-by-one fix is a source-read fix, not test-verified beyond "the full suite stayed green".

## Current risks

- Not yet committed.
- #24 (event-loop-blocking Sheets calls) remains unfixed in this file — deliberately, per
  user decision. See session-summary.md "Deferred" section.

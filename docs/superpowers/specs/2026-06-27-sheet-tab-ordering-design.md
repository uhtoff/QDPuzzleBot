# Sheet Tab Ordering Design

**Date:** 2026-06-27

## Goal

New puzzle tabs appear in the correct zone of the spreadsheet at creation time, and solving/unsolving repositions tabs to maintain the zone order automatically.

## Desired visible tab order

```
Unassigned Feeders | Evidence | REFERENCE | [unsolved targets] | [unsolved regulars] | [solved]
```

- **Unsolved targets** — metas, capstones, rounds (puzzles where `is_target=True`)
- **Unsolved regulars** — feeder/ordinary puzzles (`is_target=False`)
- **Solved** — any solved puzzle, regardless of type (already green-tabbed at end)

New placement:
- New target/meta/round → insert after the last existing unsolved-target tab; or at `INITIAL_OFFSET` if none
- New regular puzzle → insert after the last existing unsolved-regular tab; then after last target; then at `INITIAL_OFFSET`
- On solve → `move_sheet_to_end` (already correct, no change)
- On unsolve → reposition to the correct unsolved zone (same algorithm as creation)

Additional sheets (extra tabs attached to a puzzle) follow the same restore zone as the main tab.

## Out of scope

- Reordering tabs within a zone (e.g. alphabetically within targets)
- Any changes to how solved tabs are ordered among themselves
- `create_additional_spreadsheet` insertion (adjacent to puzzle's own tab — unchanged)
- The hidden template tabs (Tab template, Meta Tab template, etc.) are part of the 7 `INITIAL_OFFSET` reserved slots and do not affect this logic

## Architecture

### New method: `GoogleSheets.compute_insert_index`

```python
def compute_insert_index(self, is_target: bool, puzzles) -> int:
```

Reads the sheet list once. Builds two sets from the puzzles list:
- `target_ids` — `google_page_id` of unsolved targets (`is_target=True, not solved`)
- `regular_ids` — `google_page_id` of unsolved regulars (`is_target=False, not solved`)

Walks sheets in index order; tracks `last_target_idx` and `last_regular_idx` (highest index seen in each set, ignoring hidden sheets).

Return value:
- `is_target=True` → `last_target_idx + 1` or `INITIAL_OFFSET`
- `is_target=False` → `last_regular_idx + 1`, else `last_target_idx + 1`, else `INITIAL_OFFSET`

Puzzles with no `google_page_id` (no sheet yet) are naturally skipped.

### Signature changes in `puzzles_gsheet.py`

- `create_puzzle_spreadsheet(puzzle_data, index=None)` — uses passed index; falls back to `INITIAL_OFFSET`
- `restore_puzzle_spreadsheet(puzzle_data, restore_index=None)` — replaces both `move_sheet_to_start(INITIAL_OFFSET)` calls (main tab and additional sheets) with `move_sheet_to_index(restore_index if restore_index is not None else INITIAL_OFFSET)`

Add `move_sheet_to_index(sheet_id, index)` as the general primitive. Keep `move_sheet_to_start(sheet_id)` as a thin wrapper calling `move_sheet_to_index(sheet_id, INITIAL_OFFSET)` so existing callers outside `restore_puzzle_spreadsheet` are unchanged.

### Changes in `puzzle_service.py` — 4 call sites

| Site | Method | Action |
|------|--------|--------|
| `create_puzzle` | `create_puzzle_spreadsheet` | fetch puzzles (new puzzle not yet committed), compute index, pass |
| `create_mini_puzzle` | `create_puzzle_spreadsheet` | same; minis are regular (non-target) |
| `_reconcile_tabs` | `add_new_puzzle_sheet` | same |
| `unsolve` | `restore_puzzle_spreadsheet` | fetch puzzles, exclude puzzle being restored from computation (its sheet is still at end), compute restore_idx, pass |

**Why exclude the restored puzzle from `unsolve` computation:** at call time the puzzle's `solved=False` is already committed, so `get_all_from_hunt` returns it as unsolved. But its sheet is still at the end (solved zone). Including it would set `last_regular_idx` (or `last_target_idx`) to the end position, producing `end+1` as the insert point — wrong.

## Relevant files

- `bot/cogs/puzzles_gsheet.py` — `compute_insert_index`, `create_puzzle_spreadsheet`, `restore_puzzle_spreadsheet`, `move_sheet_to_start`
- `bot/core/puzzle_service.py` — `create_puzzle`, `create_mini_puzzle` (~line 1408), `_reconcile_tabs`, `unsolve`

## Constraints

- `compute_insert_index` makes one `sheet_list()` call (one Sheets API read) — not one per puzzle
- No change to `archive_puzzle_spreadsheet` (solve path already correct)
- No change to `create_additional_spreadsheet` (extra sheets stay adjacent to their puzzle)
- No change to `INITIAL_OFFSET` value
- No DB coupling added to the gsheet cog (the service fetches puzzles and passes them in)

## Acceptance criteria

- Creating a meta/round adds its tab immediately after the last unsolved meta tab (or at slot 7 if none)
- Creating a regular puzzle adds its tab after all unsolved regular tabs (or after last meta, or slot 7)
- Solving a puzzle moves its tab to the end (green)
- Unsolving a puzzle moves its tab back into the correct unsolved zone (target zone or regular zone), not slot 7 blindly
- Additional sheets on an unsolved puzzle follow the same restore zone as the main tab

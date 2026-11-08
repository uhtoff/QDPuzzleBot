# Compact Roll-up Tables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop bot-managed Sheets tables accumulating blank rows: a member whose
row key changes keeps its row, and a row whose member has left is deleted with
the rows below shifting up — across the Table's full width, so solver columns
inside the Table move with their row.

**Architecture:** Split `GoogleSheets.reconcile_table` into a pure row planner
(`bot/core/table_reconcile.py`: what to update, delete, append, and where the
table ends) and the existing request builder, which turns that plan into one
`batchUpdate`. Removal switches from "blank the bot's columns" to a
`deleteRange` with `shiftDimension: ROWS` over the Table's full column span.

**Tech Stack:** Python 3.12, Google Sheets API v4 (`batchUpdate`), pytest.

**Spec:** Agreed in conversation on 2026-11-07 (no separate spec doc). The
decisions this plan implements:
1. Solvers keep notes in columns *inside* the Table, right of Content (now in
   `docs/user-guide.md`, "Working notes on a roll-up"). In-Table columns are
   row-owned data and must move with their row.
2. **Re-key in place:** when a contribution row's key changes but it is the
   same member (e.g. a capstone→meta convert turns `informs:12` into
   `feeds:12`), the existing row is rewritten with the new key — no blank,
   no append, notes stay put.
3. **Delete departed rows:** a pruned row (and any already-blank row left by
   the old behaviour) is removed with `deleteRange`, cells below shifting up,
   spanning the Table's full width (`key_col`..`end_col`), never the whole sheet row.
4. A row with no key but solver content in an in-Table column (a note stranded
   by the old blank-in-place behaviour) is **kept**, never deleted.

## Global Constraints

- Never `deleteDimension` (full-width sheet rows) — only `deleteRange` bounded to `key_col`..`end_col`.
- Never write or delete outside the Table's own row extent (`startRowIndex`..`endRowIndex`) except appends directly below it.
- One `batchUpdate` per reconcile (writes are the 60/min-capped resource), requests in this order: updates, deletes (bottom-up), appends, `updateTable`, grey-below formatting.
- All four callers keep working unchanged: contribution (`render_contribution`), minimeta (`render_minimeta_contribution`), Evidence (`puzzle_service.py:3590`), Unassigned (`puzzle_service.py:3881`).
- Run tests as `timeout 180 pipenv run python -m pytest <target> -q > /tmp/... 2>&1`; `basedpyright bot/` must stay at 0 errors (CI gate).

## Review Focus

1. A keyless row holding a solver note in an in-Table column (stranded by the old behaviour) must survive — Task 1 `test_stranded_note_row_is_kept`.
2. Several rows collapsing to one member (`informs:12:5`, `informs:12:6` → `feeds:12`): the topmost is re-keyed, the rest deleted — Task 1 `test_many_rows_one_member_rekeys_topmost`.
3. Hunt-level evidence keys (`informs:free:55`) and integer keys (Evidence/Unassigned) never pair with anything — Task 1 `test_non_member_keys_never_rekey`.
4. Deleting several rows in one batch: each `deleteRange` index must be valid when it runs, so deletes are bottom-up and appends land below the *compacted* table — Task 2 `test_deletes_bottom_up_and_append_after_compaction`.
5. A Table whose live behaviour on `deleteRange` differs from the assumption (auto-shrink vs not) — Task 0 settles it before code; Task 2 always sends an explicit `updateTable` with the final range so either behaviour ends consistent.

---

### Task 0: Live probe — `deleteRange` inside a native Table

Not code for the repo. Settles the one behaviour mocks can't: what a
`deleteRange` (`shiftDimension: ROWS`) over a banded native Table's data rows
does to the Table's range and banding.

**Needs user approval first:** it creates a throwaway spreadsheet with the
service account (`google_secrets.json`) and deletes it afterwards.

- [ ] **Step 1:** In the scratchpad, write a script that creates a spreadsheet, writes a 4-column header + 5 keyed rows plus an extra in-Table column E with distinct notes, `addTable` over A1:E6, then sends one `batchUpdate` with `deleteRange` rows 2..3 (0-based 2) columns A..E `shiftDimension: ROWS`, then reads back the Table range (`spreadsheets.get` with `tables`) and values A1:F8.
- [ ] **Step 2:** Run it; record in this plan, under Task 0: (a) did the Table range shrink by itself, (b) did each note in E stay beside its key, (c) did anything right of the Table (column F) move, (d) did banding stay continuous. Delete the spreadsheet.
- [ ] **Step 3:** If (b) fails or (c) moves, stop and re-plan — the design assumption is wrong.

### Task 1: Pure row planner with re-key pairing

**Files:**
- Create: `bot/core/table_reconcile.py`
- Test: `tests/test_table_reconcile_plan.py`

**Interfaces:**
- Produces:
  - `member_identity(key: str) -> str | None` — `"feeds:12"`, `"none:12"`, `"informs:12"`, `"informs:12:55"` → `"12"`; anything else (`"informs:free:55"`, `"7"`, `""`) → `None`.
  - `@dataclass RowPlan: updates: list[tuple[int, list]]` (grid row, full desired row incl. key), `deletes: list[int]` (grid rows, **descending**), `appends: list[list]`, `append_start: int` (grid row of the first append *after* deletes), `end_row: int` (exclusive end of the Table after the batch).
  - `plan_rows(rows: list[tuple[int, list[str]]], desired_rows: list[list], header_row: int) -> RowPlan` — `rows` is every data row inside the Table's current extent as `(grid_row, cells)` where `cells[0]` is the key cell and the rest are the remaining in-Table cells (all as read back, strings, possibly short lists).

Rules `plan_rows` implements:
- Keyed rows: desired key present → update in place; absent → candidate prune.
- Re-key: walk candidate prunes top-down; each with a non-`None` identity takes the first not-yet-taken *append* (desired key not on the sheet) with the same identity, in desired order → becomes an update at that grid row. Unpaired prunes → delete.
- Keyless rows: all cells empty → delete; any non-empty cell → keep (stranded note).
- `append_start = header_row + 1 + (number of data rows in extent − len(deletes))`; `end_row = append_start + len(appends)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_table_reconcile_plan.py`)

```python
from bot.core.table_reconcile import member_identity, plan_rows

def test_member_identity():
    assert member_identity("feeds:12") == "12"
    assert member_identity("informs:12:55") == "12"
    assert member_identity("none:12") == "12"
    assert member_identity("informs:free:55") is None
    assert member_identity("7") is None and member_identity("") is None

def test_rekeys_changed_edge_in_place():
    p = plan_rows([(5, ["informs:12", "x"]), (6, ["informs:13", "y"])],
                  [["feeds:12", "One"], ["feeds:13", "Two"]], header_row=4)
    assert p.updates == [(5, ["feeds:12", "One"]), (6, ["feeds:13", "Two"])]
    assert p.deletes == [] and p.appends == []
    assert p.end_row == 7

def test_departed_rows_deleted_bottom_up_and_appends_after_compaction():
    p = plan_rows([(5, ["feeds:1"]), (6, ["feeds:2"]), (7, ["feeds:3"]), (8, ["feeds:4"])],
                  [["feeds:2", "B"], ["feeds:4", "D"], ["feeds:9", "Z"]], header_row=4)
    assert p.deletes == [7, 5]
    assert p.updates == [(6, ["feeds:2", "B"]), (8, ["feeds:4", "D"])]
    assert p.appends == [["feeds:9", "Z"]]
    assert p.append_start == 7 and p.end_row == 8

def test_old_blank_rows_deleted_but_stranded_note_row_is_kept():
    p = plan_rows([(5, ["", "", "", ""]), (6, ["", "", "", "", "my note"]),
                   (7, ["feeds:2"])], [["feeds:2", "B"]], header_row=4)
    assert p.deletes == [5]          # 6 holds a solver note in an in-Table column
    assert p.updates == [(7, ["feeds:2", "B"])]

def test_many_rows_one_member_rekeys_topmost():
    p = plan_rows([(5, ["informs:12:5"]), (6, ["informs:12:6"])],
                  [["feeds:12", "One"]], header_row=4)
    assert p.updates == [(5, ["feeds:12", "One"])] and p.deletes == [6]

def test_non_member_keys_never_rekey():
    p = plan_rows([(1, ["7"]), (2, ["informs:free:55"])],
                  [["8", "x"], ["informs:free:56", "y"]], header_row=0)
    assert p.deletes == [2, 1] and len(p.appends) == 2

def test_stranded_note_row_is_kept():
    p = plan_rows([(5, ["", "", "", "", "note"])], [], header_row=4)
    assert p.deletes == [] and p.end_row == 6
```

- [ ] **Step 2:** Run `timeout 60 pipenv run python -m pytest tests/test_table_reconcile_plan.py -q` — expect ImportError / failures.
- [ ] **Step 3:** Implement `member_identity`, `RowPlan`, `plan_rows` in `bot/core/table_reconcile.py` per the rules above (no Sheets calls, no I/O).
- [ ] **Step 4:** Re-run — all pass. Mutation check: make `plan_rows` pair prunes bottom-up instead of top-down; `test_many_rows_one_member_rekeys_topmost` must fail. Restore.
- [ ] **Step 5:** Commit `feat(sheets): pure row planner for table reconcile (re-key + delete)`.

### Task 2: `reconcile_table` uses the planner — re-key in place, delete with shift-up

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py` — `reconcile_table` (≈1534–1739): read, prune, append, resize sections and the docstring
- Modify: `tests/test_contribution_table_requests.py` — `test_reconcile_table_appends_updates_prunes` (≈101) and the `_capture_lowlevel` fake
- Modify: `docs/user-guide.md` — one sentence under "Working notes on a roll-up"

**Interfaces:**
- Consumes: `plan_rows`, `RowPlan` from Task 1.
- Produces: no signature change to `reconcile_table`.

Changes:
- Read the Table's full width (`{key_letter}{header_row+1}:{end_letter}{endRowIndex}` where `end_letter = _col_letter(end_col - 1)`, rows bounded to the Table extent; no Table → column-A read as today) and build `rows` for `plan_rows` (grid row = `header_row + 1 + i`; pad short lists).
- Updates: `updateCells` over `key_col`..`key_col + data_col_count` (unchanged shape).
- Deletes: one `deleteRange` per row in `plan.deletes` order (already descending): `{"range": {sheetId, startRowIndex: r, endRowIndex: r+1, startColumnIndex: key_col, endColumnIndex: end_col}, "shiftDimension": "ROWS"}`.
- Appends: `updateCells` starting at `plan.append_start`.
- `updateTable` end = `plan.end_row`; grey-below and fill-clear use `plan.end_row` in place of `end_row`.
- Docstring: replace the "Pruned rows are blanked in place … never deleted" paragraph with the new rule (full-width-of-Table delete, why not `deleteDimension`, re-key, stranded notes kept).
- User guide: after "…stays with that puzzle as the bot updates the table." add "When a puzzle leaves the table, its row — your columns included — is removed."

- [ ] **Step 1: Rewrite the pinned test** — `test_reconcile_table_appends_updates_prunes` asserted blank-in-place; the contract changes deliberately (spec decision 3). Keep its update and append assertions, replace the prune assertion: `informs:9` (row 2) now yields `{"deleteRange": {"range": {...startRowIndex: 2, endRowIndex: 3, startColumnIndex: 0, endColumnIndex: 4}, "shiftDimension": "ROWS"}}`; still assert no `deleteDimension`; `none:3` appends at row 2 (compacted), not row 3. Update `_capture_lowlevel`'s `fake_values_get` to return full-width rows.
- [ ] **Step 2: Add** in the same file:
  - `test_reconcile_rekeys_capstone_row_in_place` — sheet `["informs:12", …]` at row 5 with a Table at header row 4, desired `["feeds:12", …]` → one `updateCells` at row 5 writing key `feeds:12`; no `deleteRange`, no append.
  - `test_deletes_bottom_up_and_append_after_compaction` — sheet rows 5–8 `feeds:1..4`, desired `feeds:2, feeds:4, feeds:9` → `deleteRange` rows 7 then 5 in that order, all `deleteRange`s before the append, append at row 7, `updateTable.endRowIndex == 8`, every `deleteRange` bounded to the Table's `startColumnIndex`/`endColumnIndex` (use a Table with `endColumnIndex` 6 to prove the solver column is included).
  - `test_stranded_note_row_survives_reconcile` — keyless row with text in column F (inside a 6-wide Table) gets no `deleteRange`.
- [ ] **Step 3:** Run `timeout 120 pipenv run python -m pytest tests/test_contribution_table_requests.py -q` — new tests fail.
- [ ] **Step 4:** Implement the changes above.
- [ ] **Step 5:** Run the file, then the four callers' suites: `timeout 180 pipenv run python -m pytest tests/test_contribution_table_requests.py tests/test_table_reconcile_plan.py tests/test_unassigned*.py tests/test_evidence*.py tests/test_minimeta*.py -q` — pass. Then `timeout 300 pipenv run basedpyright bot/` — 0 errors. Then the full suite.
- [ ] **Step 6:** Mutation check: change the delete `endColumnIndex` to `key_col + data_col_count`; `test_deletes_bottom_up_and_append_after_compaction` must fail. Restore.
- [ ] **Step 7:** Commit `feat(sheets): roll-up tables re-key rows in place and delete departed rows`.

### Task 3: Live verification on the test instance

- [ ] **Step 1:** Push to `staging` (user's go-ahead) and, on a throwaway test hunt: capstone with 2 informs feeders, add a note column inside the Table right of Content, write a note on each row, `!convert_to_meta` → rows re-keyed in place, notes still beside their puzzles, no blank rows.
- [ ] **Step 2:** `!remove_from CODE` on one feeder → its row (and note) disappears, rows below move up, nothing right of the Table moves.
- [ ] **Step 3:** On the "Test Hunt" First Round table with old blank rows, `!rebuild` → blank rows gone.

# Current Spec — Sheet Tab Ordering

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-3
Phase 1 model: Opus 4.8
Phase 2 model: Sonnet 4.6
Started: 2026-06-27
Status: ready for handoff — Phase 1 DONE @ b58b098; Phase 2 next (Sonnet 4.6)

---

## Before you start (executing model)

- Execute **only the current phase**. Do not implement production code in Phase 1.
- On any unexpected failure: invoke `superpowers:systematic-debugging` before changing anything.
- Before claiming a phase done: invoke `superpowers:verification-before-completion` and paste the acceptance command output.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write `.ai/active/blocker.md` and STOP.

---

## Goal

New Google Sheets puzzle tabs land in the correct zone at creation time, and solve/unsolve repositions them to maintain the zone order.

**Zone order (visible tabs):**
```
Unassigned Feeders | Evidence | REFERENCE | [unsolved targets] | [unsolved regulars] | [solved]
```

- **Unsolved targets** — puzzles where `is_target=True` and `solved=False`
- **Unsolved regulars** — puzzles where `is_target=False` and `solved=False`
- **Solved** — any solved puzzle (already handled correctly by `move_sheet_to_end`)

---

## In scope

1. New method `GoogleSheets.compute_insert_index(is_target, puzzles) -> int`
2. New helper `GoogleSheets.move_sheet_to_index(sheet_id, index)` — the general primitive; `move_sheet_to_start` becomes a thin wrapper
3. `create_puzzle_spreadsheet(puzzle_data, index=None)` — accept optional index
4. `restore_puzzle_spreadsheet(puzzle_data, restore_index=None)` — use `restore_index` in place of hard-coded `INITIAL_OFFSET` for both main tab and additional sheets
5. Four call sites in `puzzle_service.py`: `create_puzzle`, `create_mini_puzzle`, `_reconcile_tabs`, `unsolve`

## Out of scope

- Reordering tabs within a zone (e.g. alphabetically)
- Sorting solved tabs among themselves
- `create_additional_spreadsheet` (extra sheets stay adjacent to their puzzle — unchanged)
- Any changes to `INITIAL_OFFSET` value (stays 7)
- Hidden template tabs (Tab template, Meta Tab template, Overview, etc.) — unaffected
- `archive_puzzle_spreadsheet` (solve path uses `move_sheet_to_end` — already correct, no change)

---

## Relevant files

| File | Role |
|------|------|
| `bot/cogs/puzzles_gsheet.py` | New method + changed signatures (read/write) |
| `bot/core/puzzle_service.py` | 4 call sites updated (read/write) |
| `tests/test_sheet_service.py` | Existing sheet tests (read — do not modify) |
| `tests/test_create_service.py` | Existing create tests (read — do not modify) |
| `tests/test_reconcile.py` | Existing reconcile tests (read — do not modify) |
| `tests/conftest.py` | Factories: `make_hunt()`, `make_puzzle()`, `make_cog()` (read only) |
| `docs/superpowers/specs/2026-06-27-sheet-tab-ordering-design.md` | Design spec (read only) |

---

## Constraints

- Do not touch any file outside the list above.
- Do not refactor existing methods beyond what is needed for each change.
- `move_sheet_to_start(sheet_id)` must remain working (keep it as a wrapper calling `move_sheet_to_index(sheet_id, self.INITIAL_OFFSET)`) — it is called outside `restore_puzzle_spreadsheet`.
- The `compute_insert_index` signature takes `is_target: bool` and `puzzles` (iterable of `PuzzleData`). It calls `self.sheet_list()` internally — one API call per invocation.

---

## Test constraints (anti-patterns — follow these exactly)

- **Reuse exact helpers/seams from `conftest.py`; invent none.** The helpers are plain functions: `make_hunt()`, `make_puzzle()`, `make_cog()`. Call them; they are NOT pytest fixtures.
- **Never write a loop that resolves decisions/state "until" a condition.** If you think you need one, STOP and write `blocker.md`.
- **Never modify test infrastructure.**
- **Confirm every attribute exists before using it** — grep `puzzle_data.py` and `puzzles_gsheet.py` for field names. Do not guess.
- **A test file must define or import every helper it calls.** Never rely on a helper defined in a sibling test file. `--filter` hides the gap; the parallel gate shards by file and a cross-file helper becomes a fatal.
- **Wrap every test run in `timeout`, write output to a file, single statement.**
- `asyncio_mode = auto` is set in `pytest.ini` — write `async def test_*` without decorators; await service calls directly.
- **Mock the Sheets API at the import site:** patch `bot.cogs.puzzles_gsheet.get_sheet` (not the underlying google library). `sheet_list()` calls `execute_sync(get_sheet().get(...))` — the mock must make `execute_sync` return immediately without real HTTP. In existing tests, `execute_sync` is patched directly (`patch("bot.cogs.puzzles_gsheet.execute_sync", side_effect=lambda req: req)`).
- **`batch_update` is awaitable** — patch it as `AsyncMock`.

---

## Phase 1 — Write all failing tests

**Target file:** `tests/test_sheet_ordering.py`

Run (after writing): confirm the file imports without error AND all tests fail for the right reason (method does not exist yet).

```bash
timeout 60 pipenv run python -m pytest tests/test_sheet_ordering.py -v > /tmp/test-ordering.log 2>&1; echo "exit: $?"
```

Expected: exit non-zero, every test shows `FAILED` or `ERROR` with `AttributeError: 'GoogleSheets' object has no attribute 'compute_insert_index'` (or similar — method not yet implemented). NOT a bootstrap/import fatal.

### Test skeletons

Read `tests/conftest.py` to verify the exact signatures of `make_hunt()`, `make_puzzle()`, `make_cog()` before writing. Read `bot/store/puzzle_data.py` to verify the field names (`google_page_id`, `is_target`, `solved`).

```python
# tests/test_sheet_ordering.py
"""Tests for GoogleSheets.compute_insert_index and zone-aware insertion/restore."""

from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from tests.conftest import make_hunt, make_puzzle, make_cog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sheets_cog():
    """Return a GoogleSheets cog with mocked Sheets API."""
    # make_cog() creates the full Puzzles cog; we need the GoogleSheets cog.
    # Instantiate it directly, mocking the service build.
    from bot.cogs.puzzles_gsheet import GoogleSheets
    with patch("bot.cogs.puzzles_gsheet.get_sheet") as mock_get_sheet:
        mock_get_sheet.return_value = MagicMock()
        cog = GoogleSheets.__new__(GoogleSheets)
        cog.sheets_service = mock_get_sheet.return_value
    return cog


def _sheet_entry(sheet_id, index, hidden=False):
    """Build a minimal sheet entry as returned by sheet_list()."""
    return {
        "properties": {
            "sheetId": sheet_id,
            "index": index,
            "hidden": hidden,
            "title": f"Sheet {sheet_id}",
        }
    }


def _make_puzzle_with_sheet(page_id, is_target, solved):
    p = make_puzzle()
    p.google_page_id = page_id
    p.is_target = is_target
    p.solved = solved
    return p


# ---------------------------------------------------------------------------
# compute_insert_index: no existing puzzle tabs → INITIAL_OFFSET
# ---------------------------------------------------------------------------

def test_compute_insert_index_no_puzzles_target(tmp_path):
    cog = make_sheets_cog()
    # Sheet list has only reserved slots (indices 0-6), no puzzle tabs
    reserved = [_sheet_entry(i, i) for i in range(7)]
    with patch.object(cog, "sheet_list", return_value={"sheets": reserved}):
        idx = cog.compute_insert_index(is_target=True, puzzles=[])
    assert idx == cog.INITIAL_OFFSET


def test_compute_insert_index_no_puzzles_regular(tmp_path):
    cog = make_sheets_cog()
    reserved = [_sheet_entry(i, i) for i in range(7)]
    with patch.object(cog, "sheet_list", return_value={"sheets": reserved}):
        idx = cog.compute_insert_index(is_target=False, puzzles=[])
    assert idx == cog.INITIAL_OFFSET


# ---------------------------------------------------------------------------
# compute_insert_index: target goes after last unsolved target
# ---------------------------------------------------------------------------

def test_compute_insert_index_target_after_existing_target():
    cog = make_sheets_cog()
    target_puzzle = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    # Target is at sheet index 7
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(101, 7)]
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=True, puzzles=[target_puzzle])
    assert idx == 8   # after the target at index 7


def test_compute_insert_index_target_after_last_of_multiple_targets():
    cog = make_sheets_cog()
    t1 = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    t2 = _make_puzzle_with_sheet(page_id=102, is_target=True, solved=False)
    sheets = (
        [_sheet_entry(i, i) for i in range(7)]
        + [_sheet_entry(101, 7), _sheet_entry(102, 8)]
    )
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=True, puzzles=[t1, t2])
    assert idx == 9   # after index 8


# ---------------------------------------------------------------------------
# compute_insert_index: regular puzzle positioning
# ---------------------------------------------------------------------------

def test_compute_insert_index_regular_after_existing_regular():
    cog = make_sheets_cog()
    r1 = _make_puzzle_with_sheet(page_id=201, is_target=False, solved=False)
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(201, 7)]
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=False, puzzles=[r1])
    assert idx == 8


def test_compute_insert_index_regular_after_target_when_no_regulars():
    """With only a target existing, a new regular goes right after the target."""
    cog = make_sheets_cog()
    t1 = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(101, 7)]
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=False, puzzles=[t1])
    assert idx == 8


def test_compute_insert_index_regular_after_last_regular_not_last_target():
    """Regular goes after the last regular, even if there's a target later
    (shouldn't happen in normal use but the algorithm must handle it)."""
    cog = make_sheets_cog()
    t1 = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    r1 = _make_puzzle_with_sheet(page_id=201, is_target=False, solved=False)
    # target at 7, regular at 8
    sheets = (
        [_sheet_entry(i, i) for i in range(7)]
        + [_sheet_entry(101, 7), _sheet_entry(201, 8)]
    )
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=False, puzzles=[t1, r1])
    assert idx == 9  # after last regular (8), not last target (7)


# ---------------------------------------------------------------------------
# compute_insert_index: solved tabs are excluded from zone anchors
# ---------------------------------------------------------------------------

def test_compute_insert_index_ignores_solved_puzzles():
    """A solved puzzle's tab (at the end) must not anchor the unsolved zone."""
    cog = make_sheets_cog()
    solved = _make_puzzle_with_sheet(page_id=301, is_target=False, solved=True)
    # Solved tab is at index 20 (end); no unsolved tabs exist
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(301, 20)]
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=False, puzzles=[solved])
    assert idx == cog.INITIAL_OFFSET  # falls back, not 21


# ---------------------------------------------------------------------------
# compute_insert_index: hidden sheets skipped
# ---------------------------------------------------------------------------

def test_compute_insert_index_skips_hidden_sheets():
    cog = make_sheets_cog()
    r1 = _make_puzzle_with_sheet(page_id=201, is_target=False, solved=False)
    # r1's sheet is hidden (shouldn't happen in practice but ensure robustness)
    sheets = [_sheet_entry(201, 7, hidden=True)]
    with patch.object(cog, "sheet_list", return_value={"sheets": sheets}):
        idx = cog.compute_insert_index(is_target=False, puzzles=[r1])
    assert idx == cog.INITIAL_OFFSET  # hidden tab not counted


# ---------------------------------------------------------------------------
# create_puzzle_spreadsheet: uses passed index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_puzzle_spreadsheet_uses_passed_index():
    """When an index is passed, add_new_puzzle_sheet receives that index."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.is_target = False

    with (
        patch.object(cog, "add_new_puzzle_sheet", new_callable=AsyncMock) as mock_add,
        patch.object(cog, "update_puzzle_info", new_callable=AsyncMock),
    ):
        await cog.create_puzzle_spreadsheet(puzzle, index=12)
    mock_add.assert_awaited_once_with(puzzle, 12)


@pytest.mark.asyncio
async def test_create_puzzle_spreadsheet_defaults_to_initial_offset():
    """When no index is passed, falls back to INITIAL_OFFSET."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()

    with (
        patch.object(cog, "add_new_puzzle_sheet", new_callable=AsyncMock) as mock_add,
        patch.object(cog, "update_puzzle_info", new_callable=AsyncMock),
    ):
        await cog.create_puzzle_spreadsheet(puzzle)
    mock_add.assert_awaited_once_with(puzzle, cog.INITIAL_OFFSET)


# ---------------------------------------------------------------------------
# move_sheet_to_index: builds correct request
# ---------------------------------------------------------------------------

def test_move_sheet_to_index_request():
    cog = make_sheets_cog()
    req = cog.move_sheet_to_index(sheet_id=42, index=5)
    assert req["updateSheetProperties"]["properties"]["sheetId"] == 42
    assert req["updateSheetProperties"]["properties"]["index"] == 5


def test_move_sheet_to_start_delegates_to_initial_offset():
    cog = make_sheets_cog()
    req = cog.move_sheet_to_start(sheet_id=42)
    assert req["updateSheetProperties"]["properties"]["index"] == cog.INITIAL_OFFSET


# ---------------------------------------------------------------------------
# restore_puzzle_spreadsheet: uses restore_index for main + additional tabs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_puzzle_spreadsheet_uses_restore_index():
    """When restore_index is passed, the move request targets that index."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = False
    puzzle.archived = False
    puzzle.additional_sheets = []

    captured_requests = []

    async def fake_batch_update(body, archive=False):
        captured_requests.extend(body.get("requests", []))

    with (
        patch.object(cog, "batch_update", side_effect=fake_batch_update),
        patch.object(cog, "get_spreadsheet_id", return_value="sheet-id"),
        patch.object(cog, "get_archive_spreadsheet_id", return_value=None),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        mock_cfg.puzzle_cell_solution = "B1"
        mock_cfg.puzzle_cell_progress = "B3"
        await cog.restore_puzzle_spreadsheet(puzzle, restore_index=9)

    move_reqs = [
        r for r in captured_requests
        if "updateSheetProperties" in r
        and "index" in r["updateSheetProperties"]["properties"]
    ]
    assert move_reqs, "Expected at least one index-setting updateSheetProperties request"
    for req in move_reqs:
        assert req["updateSheetProperties"]["properties"]["index"] == 9, (
            f"Expected index 9 but got {req['updateSheetProperties']['properties']['index']}"
        )


@pytest.mark.asyncio
async def test_restore_puzzle_spreadsheet_defaults_to_initial_offset():
    """When restore_index is omitted, falls back to INITIAL_OFFSET."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = False
    puzzle.archived = False
    puzzle.additional_sheets = []

    captured_requests = []

    async def fake_batch_update(body, archive=False):
        captured_requests.extend(body.get("requests", []))

    with (
        patch.object(cog, "batch_update", side_effect=fake_batch_update),
        patch.object(cog, "get_spreadsheet_id", return_value="sheet-id"),
        patch.object(cog, "get_archive_spreadsheet_id", return_value=None),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        mock_cfg.puzzle_cell_solution = "B1"
        mock_cfg.puzzle_cell_progress = "B3"
        await cog.restore_puzzle_spreadsheet(puzzle)

    move_reqs = [
        r for r in captured_requests
        if "updateSheetProperties" in r
        and "index" in r["updateSheetProperties"]["properties"]
    ]
    for req in move_reqs:
        assert req["updateSheetProperties"]["properties"]["index"] == cog.INITIAL_OFFSET
```

### Phase 1 acceptance

```bash
timeout 60 pipenv run python -m pytest tests/test_sheet_ordering.py -v > /tmp/test-ordering.log 2>&1; echo "exit: $?"
cat /tmp/test-ordering.log
```

All tests must **fail** (not error with a bootstrap fatal). Acceptable fail reasons:
- `AttributeError: 'GoogleSheets' object has no attribute 'compute_insert_index'`
- `AttributeError: 'GoogleSheets' object has no attribute 'move_sheet_to_index'`
- `TypeError` (wrong number of arguments to existing method)

If any test produces an **import error or bootstrap fatal**, fix the test (wrong helper name, wrong attribute name, etc.) — do NOT add scaffolding to make it pass.

---

## Phase 2 — Implement in `puzzles_gsheet.py`

**Pre-check:** `grep -n "move_sheet_to_start\|create_puzzle_spreadsheet\|restore_puzzle_spreadsheet" bot/cogs/puzzles_gsheet.py` — note all call sites.

### Step 2.1 — Add `move_sheet_to_index`

In `puzzles_gsheet.py`, find `move_sheet_to_start`:

```python
def move_sheet_to_start(self, sheet_id = None):
    return self._sheet_props_request(sheet_id, 'index', self.INITIAL_OFFSET)
```

Add a new method above it:

```python
def move_sheet_to_index(self, sheet_id, index):
    return self._sheet_props_request(sheet_id, 'index', index)
```

Change `move_sheet_to_start` to delegate:

```python
def move_sheet_to_start(self, sheet_id = None):
    return self.move_sheet_to_index(sheet_id, self.INITIAL_OFFSET)
```

### Step 2.2 — Add `compute_insert_index`

Add after `move_sheet_to_end`:

```python
def compute_insert_index(self, is_target: bool, puzzles) -> int:
    """Return the correct 0-based tab insertion index for a new puzzle.

    Target puzzles land after the last unsolved-target tab.
    Regular puzzles land after the last unsolved-regular tab (or the last
    target, if no regulars exist). Both fall back to INITIAL_OFFSET when
    no unsolved puzzle tabs exist yet.
    """
    target_ids = {
        str(p.google_page_id) for p in puzzles
        if getattr(p, 'is_target', False) and not p.solved and p.google_page_id
    }
    regular_ids = {
        str(p.google_page_id) for p in puzzles
        if not getattr(p, 'is_target', False) and not p.solved and p.google_page_id
    }
    last_target_idx = None
    last_regular_idx = None
    for sheet in self.sheet_list()["sheets"]:
        props = sheet["properties"]
        if props.get("hidden"):
            continue
        sid = str(props["sheetId"])
        idx = props["index"]
        if sid in target_ids:
            if last_target_idx is None or idx > last_target_idx:
                last_target_idx = idx
        elif sid in regular_ids:
            if last_regular_idx is None or idx > last_regular_idx:
                last_regular_idx = idx
    if is_target:
        return (last_target_idx + 1) if last_target_idx is not None else self.INITIAL_OFFSET
    if last_regular_idx is not None:
        return last_regular_idx + 1
    if last_target_idx is not None:
        return last_target_idx + 1
    return self.INITIAL_OFFSET
```

### Step 2.3 — Update `create_puzzle_spreadsheet`

Change signature and body:

```python
async def create_puzzle_spreadsheet(self, puzzle_data: PuzzleData, index=None):
    puzzle_index = index if index is not None else self.INITIAL_OFFSET
    await self.add_new_puzzle_sheet(puzzle_data, puzzle_index)
    await self.update_puzzle_info(puzzle_data)
```

### Step 2.4 — Update `restore_puzzle_spreadsheet`

Add `restore_index=None` parameter. Replace **both** occurrences of `self.move_sheet_to_start(...)` with `self.move_sheet_to_index(..., restore_index if restore_index is not None else self.INITIAL_OFFSET)`.

There are two calls:
1. Inside the `for sheet in puzzle_data.additional_sheets:` block
2. For `puzzle_data.google_page_id` (the main tab)

Change both.

### Phase 2 acceptance

```bash
timeout 120 pipenv run python -m pytest tests/test_sheet_ordering.py tests/test_sheet_service.py tests/test_create_service.py -v > /tmp/test-p2.log 2>&1; echo "exit: $?"
cat /tmp/test-p2.log
```

All new tests pass; no regressions in existing sheet/create tests.

---

## Phase 3 — Wire up `puzzle_service.py`

### Step 3.1 — `create_puzzle` (line ~1546)

Find:
```python
gsheet_cog = self._gsheet_cog(hunt)
if gsheet_cog is not None:
    await gsheet_cog.create_puzzle_spreadsheet(new_puzzle)
```

Replace with:
```python
gsheet_cog = self._gsheet_cog(hunt)
if gsheet_cog is not None:
    existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
    insert_idx = gsheet_cog.compute_insert_index(new_puzzle.is_target, existing)
    await gsheet_cog.create_puzzle_spreadsheet(new_puzzle, index=insert_idx)
```

### Step 3.2 — `create_mini_puzzle` (line ~1406)

Find:
```python
gsheet_cog = self._gsheet_cog(hunt)
if gsheet_cog is not None:
    await gsheet_cog.create_puzzle_spreadsheet(mini)
```

Replace with:
```python
gsheet_cog = self._gsheet_cog(hunt)
if gsheet_cog is not None:
    existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
    insert_idx = gsheet_cog.compute_insert_index(False, existing)  # minis are regular
    await gsheet_cog.create_puzzle_spreadsheet(mini, index=insert_idx)
```

### Step 3.3 — `_reconcile_tabs` (line ~377)

Find:
```python
if apply:
    await gsheet_cog.add_new_puzzle_sheet(puzzle, gsheet_cog.INITIAL_OFFSET)
    await gsheet_cog.update_puzzle_info(puzzle)
    PuzzleJsonDb.commit(puzzle)
```

Replace with:
```python
if apply:
    existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
    insert_idx = gsheet_cog.compute_insert_index(
        getattr(puzzle, 'is_target', False), existing)
    await gsheet_cog.add_new_puzzle_sheet(puzzle, insert_idx)
    await gsheet_cog.update_puzzle_info(puzzle)
    PuzzleJsonDb.commit(puzzle)
```

### Step 3.4 — `unsolve` (line ~988)

Find:
```python
gsheet_cog = self._gsheet_cog(hunt)
if gsheet_cog is not None:
    await gsheet_cog.restore_puzzle_spreadsheet(puzzle)
```

Replace with:
```python
gsheet_cog = self._gsheet_cog(hunt)
if gsheet_cog is not None:
    # Exclude this puzzle from the zone computation: its sheet is still at
    # the solved end (not yet moved), so including it would anchor the zone
    # at the wrong position.
    existing = [p for p in PuzzleJsonDb.get_all_from_hunt(hunt.id) if p.id != puzzle.id]
    restore_idx = gsheet_cog.compute_insert_index(puzzle.is_target, existing)
    await gsheet_cog.restore_puzzle_spreadsheet(puzzle, restore_index=restore_idx)
```

### Phase 3 acceptance

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-full.log 2>&1; echo "exit: $?"
cat /tmp/test-full.log
```

Full suite must pass (exit 0). Check for any import or attribute errors.

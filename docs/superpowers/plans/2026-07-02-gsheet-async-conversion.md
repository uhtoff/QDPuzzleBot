# Google Sheets sync→async conversion (review #24) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `bot/cogs/puzzles_gsheet.py`'s blocking `sheet_list()` read (and the six/seven sync helpers layered on it) from calling `execute_sync` — which retries with a real `time.sleep` backoff of up to 60s, directly on the bot's asyncio event loop — because that stalls every Discord command, web request, and the gateway heartbeat, and can disconnect the bot mid-hunt. This is deferred review finding **#24** in `docs/reviews/2026-07-02-staging-full-review.md`.

**Architecture:** Convert `sheet_list` and every sync helper method that calls it (`get_unique_tab_name`, `get_page_id_by_name`, `get_page_name_by_id`, `move_sheet_to_end`, `compute_insert_index`, and `get_puzzle_sheet_index` — a 7th method with the identical defect, not named in the original review but sharing the same bug and breaking the moment `sheet_list` goes async) to `async def`, and switch their one blocking call from `execute_sync(...)` to `await execute_with_retry(..., on_retry=self._notify_retry)` — the async-safe equivalent that already exists in `bot/utils/gsheets.py` and is used by every other async method in this file (e.g. `batch_update` at line 92). Then thread `await` through every call site (~30 across `bot/cogs/puzzles_gsheet.py` and `bot/core/puzzle_service.py`), promoting two sync helpers/closures that wrap these methods to `async def` along the way, and update the two test files that call them directly.

Because Python cannot half-await a converted function (a synchronous caller of an `async def` gets a coroutine object back, not a result, and any subscript/iteration on it raises `TypeError`), this is not independently shippable one-function-at-a-time. The plan is split into 3 tasks, each ending in a green test run:
1. The 7 methods + every call site *within* `puzzles_gsheet.py` (the file where they live, and where 20+ of the 30 call sites are), plus that file's own tests.
2. Every call site in `bot/core/puzzle_service.py` (8 call sites across 5 methods, including two that need their own sync→async promotion).
3. Full-suite verification and a final grep sweep for anything missed.

**Tech Stack:** Python 3, discord.py (asyncio), pytest + pytest-asyncio (`asyncio_mode = auto` in `pytest.ini` — `async def test_*` needs no decorator), `unittest.mock.AsyncMock`/`patch.object`.

**Test command (from `AGENTS.md`):**
```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
Use `pipenv run python -m pytest tests/test_sheet_ordering.py tests/test_sheet_list_guard.py -q` for the two directly-affected files during Task 1, and `tests/test_reconcile.py tests/test_minimeta_service.py tests/test_aggregate_to_tab.py tests/test_unassigned_service.py -q` for Task 2 (Task 2 Step 10).

---

## Task 1: Convert the 7 methods to async + fix every call site inside `bot/cogs/puzzles_gsheet.py`

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py` (methods at lines 96–108, 110–133, 135–138, 140–143, 145–150, 351–357, 359–395, plus ~20 call sites)
- Modify: `tests/test_sheet_list_guard.py`
- Modify: `tests/test_sheet_ordering.py` (the 9 `compute_insert_index` tests + the 1 `get_page_name_by_id`-patching test)

Line numbers below are from the current file state; re-check with `grep -n` before editing if earlier edits in this task shifted them.

- [ ] **Step 1: Convert `sheet_list` to async**

Replace (lines 96–108):
```python
    def sheet_list(self, spreadsheet_id = None):
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        response = execute_sync(get_sheet().get(spreadsheetId=spreadsheet_id))
        if "sheets" not in response:
            raise RuntimeError(
                f"Sheets get for {spreadsheet_id} returned no 'sheets' field: {response!r}"
            )
        return response
```
with:
```python
    async def sheet_list(self, spreadsheet_id = None):
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        response = await execute_with_retry(
            get_sheet().get(spreadsheetId=spreadsheet_id), on_retry=self._notify_retry
        )
        if "sheets" not in response:
            raise RuntimeError(
                f"Sheets get for {spreadsheet_id} returned no 'sheets' field: {response!r}"
            )
        return response
```

- [ ] **Step 2: Convert `get_unique_tab_name` to async**

Replace (lines 110–133):
```python
    def get_unique_tab_name(self, desired_name: str, spreadsheet_id = None) -> str:
        if spreadsheet_id is None:
            existing = {
                s["properties"]["title"].casefold()
                for s in self.sheet_list()["sheets"]
            }
        else:
            meta = execute_sync(get_sheet().get(
                spreadsheetId=spreadsheet_id
            ))
            existing = {
                s["properties"]["title"].casefold()
                for s in meta["sheets"]
            }

        if desired_name.casefold() not in existing:
            return desired_name

        i = 2
        while True:
            candidate = f"{desired_name} ({i})"
            if candidate.casefold() not in existing:
                return candidate
            i += 1
```
with:
```python
    async def get_unique_tab_name(self, desired_name: str, spreadsheet_id = None) -> str:
        if spreadsheet_id is None:
            existing = {
                s["properties"]["title"].casefold()
                for s in (await self.sheet_list())["sheets"]
            }
        else:
            meta = await execute_with_retry(
                get_sheet().get(spreadsheetId=spreadsheet_id), on_retry=self._notify_retry
            )
            existing = {
                s["properties"]["title"].casefold()
                for s in meta["sheets"]
            }

        if desired_name.casefold() not in existing:
            return desired_name

        i = 2
        while True:
            candidate = f"{desired_name} ({i})"
            if candidate.casefold() not in existing:
                return candidate
            i += 1
```

- [ ] **Step 3: Convert `get_page_id_by_name` to async**

Replace (lines 135–138):
```python
    def get_page_id_by_name(self, name):
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']
```
with:
```python
    async def get_page_id_by_name(self, name):
        for sheet in (await self.sheet_list())["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']
```

- [ ] **Step 4: Convert `get_page_name_by_id` to async**

Replace (lines 140–143):
```python
    def get_page_name_by_id(self, id, spreadsheet_id = None):
        for sheet in self.sheet_list(spreadsheet_id)["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']
```
with:
```python
    async def get_page_name_by_id(self, id, spreadsheet_id = None):
        for sheet in (await self.sheet_list(spreadsheet_id))["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']
```

- [ ] **Step 5: Convert `get_puzzle_sheet_index` to async**

This method is not one of the 6 named in the review, but it has the identical defect (calls `self.sheet_list()` synchronously) and will break the moment `sheet_list` is async — its single caller is fixed in Step 12.

Replace (lines 145–150):
```python
    def get_puzzle_sheet_index(self, puzzle:PuzzleData):
        name = puzzle.name
        for sheet in self.sheet_list()["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['index']
        return self.INITIAL_OFFSET
```
with:
```python
    async def get_puzzle_sheet_index(self, puzzle:PuzzleData):
        name = puzzle.name
        for sheet in (await self.sheet_list())["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['index']
        return self.INITIAL_OFFSET
```

- [ ] **Step 6: Convert `move_sheet_to_end` to async**

Replace (lines 351–357):
```python
    def move_sheet_to_end(self, sheet_id = None):
        # The Sheets API evaluates updateSheetProperties index moves against
        # pre-move positions, so len(sheets) (not len(sheets) - 1) is the index
        # that lands the tab truly last.
        sheets = self.sheet_list().get('sheets',[])
        new_index = len(sheets)
        return self._sheet_props_request(sheet_id, 'index', new_index)
```
with:
```python
    async def move_sheet_to_end(self, sheet_id = None):
        # The Sheets API evaluates updateSheetProperties index moves against
        # pre-move positions, so len(sheets) (not len(sheets) - 1) is the index
        # that lands the tab truly last.
        sheets = (await self.sheet_list()).get('sheets',[])
        new_index = len(sheets)
        return self._sheet_props_request(sheet_id, 'index', new_index)
```

- [ ] **Step 7: Convert `compute_insert_index` to async**

Replace (lines 359–395), keeping the docstring and body logic unchanged except the signature and the `sheet_list` call:
```python
    def compute_insert_index(self, is_target: bool, puzzles) -> int:
```
with:
```python
    async def compute_insert_index(self, is_target: bool, puzzles) -> int:
```
and replace the loop line:
```python
        for sheet in self.sheet_list()["sheets"]:
```
with:
```python
        for sheet in (await self.sheet_list())["sheets"]:
```

- [ ] **Step 8: Update the trivial call sites inside already-`async def` methods**

These three just need `self.sheet_list(...)` → `await self.sheet_list(...)`, no other change:

`reconcile_table` (around line 1160):
```python
        for s in await self.sheet_list(spreadsheet_id)["sheets"]:
```
Wait — `await` binds tighter than subscript is fine, but write it explicitly parenthesized for clarity and correctness:
```python
        for s in (await self.sheet_list(spreadsheet_id))["sheets"]:
```

`_ensure_contribution_table` (around line 1456), same pattern:
```python
        for s in (await self.sheet_list(spreadsheet_id))["sheets"]:
```

- [ ] **Step 9: Fix the `render_minimeta_contribution` generator-inside-`any()` site**

Replace (around lines 1479–1483):
```python
        has_table = any(
            bool(s.get("tables"))
            for s in self.sheet_list(spreadsheet_id)["sheets"]
            if s["properties"]["sheetId"] == int(page_id)
        )
```
with:
```python
        sheets = (await self.sheet_list(spreadsheet_id))["sheets"]
        has_table = any(
            bool(s.get("tables"))
            for s in sheets
            if s["properties"]["sheetId"] == int(page_id)
        )
```

- [ ] **Step 10: Update the 4 plain `get_unique_tab_name` call sites**

In `update_puzzle_info`, `add_new_sheet`, `move_puzzle_spreadsheet`, `generate_puzzle_tab` (lines 297, 405, 506, 1343) — each currently reads `self.get_unique_tab_name(...)` as a plain statement; add `await` in place, e.g. line 297:
```python
        tab_name = await self.get_unique_tab_name(puzzle_data.name)
```
(Adjust the exact argument list per call site to match what's already there — only the `await` is new.)

- [ ] **Step 11: Update the `get_page_id_by_name` call site in `add_new_sheet`**

Around line 404:
```python
        sheet_id = await self.get_page_id_by_name(sheet_type)
```

- [ ] **Step 12: Update the `get_puzzle_sheet_index` call site in `create_additional_spreadsheet`**

Replace (around line 641):
```python
        index = self.get_puzzle_sheet_index(puzzle_data)
```
with:
```python
        index = await self.get_puzzle_sheet_index(puzzle_data)
```

- [ ] **Step 13: Update the 8 plain `get_page_name_by_id` call sites**

In `restore_puzzle_spreadsheet` (line 529), `archive_puzzle_spreadsheet` (line 588), `demote_tab_to_puzzle` (line 678), `aggregate_to_tab` (line 855), `ensure_unassigned_table` (line 924), `ensure_evidence_table` (line 995), `promote_tab_to_target` (line 1393) — add `await` in place, e.g. line 529:
```python
                    current_name = await self.get_page_name_by_id(sheet.google_page_id, self.get_archive_spreadsheet_id())
```
and line 588:
```python
                current_name = await self.get_page_name_by_id(sheet.google_page_id)
```
(Same pattern for the remaining 5 — only the `await` is new, arguments unchanged.)

- [ ] **Step 14: Hoist the two `move_sheet_to_end` calls out of `archive_puzzle_spreadsheet`'s list literals**

Replace the full method body (lines 564–590) — the two `self.move_sheet_to_end(...)` calls inside `requests.extend([...])` literals cannot stay as bare expressions once `await` is required inside a multi-line literal without hurting readability, and the second one is inside a per-sheet loop where it now triggers a real network round-trip each iteration:
```python
    async def archive_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        requests = []
        main_move_req = await self.move_sheet_to_end(puzzle_data.google_page_id)
        requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution), self.get_column(config.puzzle_cell_solution) + offset, self.STRING_INPUT, puzzle_data.google_page_id),
            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress), self.get_column(config.puzzle_cell_progress) + offset, self.STRING_INPUT, puzzle_data.google_page_id),
            self._solution_bg_request(puzzle_data, self.SOLUTION_GREEN),
            main_move_req,
            self.change_tab_colour('green', puzzle_data.google_page_id)])
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                sheet_move_req = await self.move_sheet_to_end(sheet.google_page_id)
                requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution),
                                             self.get_column(config.puzzle_cell_solution), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress),
                                             self.get_column(config.puzzle_cell_progress), self.STRING_INPUT,
                                             sheet.google_page_id),
                            sheet_move_req,
                            self.change_tab_colour('green', sheet.google_page_id)])
        updates = {
            'requests': requests
        }
        await self.batch_update(updates)
        for sheet in puzzle_data.additional_sheets:
            if self.get_archive_spreadsheet_id():
                current_name = await self.get_page_name_by_id(sheet.google_page_id)
                sheet.google_page_id = await self.move_puzzle_spreadsheet(True, sheet.google_page_id, current_name)
        if puzzle_data.is_target is False and self.get_archive_spreadsheet_id():
            puzzle_data.google_page_id = await self.move_puzzle_spreadsheet(True, puzzle_data.google_page_id, puzzle_data.name)
            puzzle_data.archived = True
            return
        else:
            return
```

- [ ] **Step 15: Run the file's own tests — expect failures (fixed in Steps 16–17)**

```bash
pipenv run python -m pytest tests/test_sheet_list_guard.py tests/test_sheet_ordering.py -q > /tmp/test.log 2>&1
```
Expected: FAIL — `test_sheet_list_guard.py`'s two tests call `cog.sheet_list(...)` without `await` (now returns an unawaited coroutine, comparisons/`pytest.raises` won't see the `RuntimeError`); `test_sheet_ordering.py`'s 9 `compute_insert_index` tests and the 1 `get_page_name_by_id`-patching test are similarly out of sync.

- [ ] **Step 16: Fix `tests/test_sheet_list_guard.py`**

Replace the full file body from the imports down (keep the module docstring at lines 1–11 unchanged):
```python
import pytest
from unittest.mock import AsyncMock, patch

import bot.cogs.puzzles_gsheet as gs
from bot.cogs.puzzles_gsheet import GoogleSheets


def _cog():
    # Skip __init__ (it builds a real Sheets service); sheet_list only touches the
    # module-level get_sheet / execute_with_retry, which we stub.
    return GoogleSheets.__new__(GoogleSheets)


async def test_sheet_list_raises_when_sheets_key_missing():
    cog = _cog()
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock(return_value={})):
        with pytest.raises(RuntimeError):
            await cog.sheet_list("SS")


async def test_sheet_list_returns_response_when_sheets_present():
    cog = _cog()
    payload = {"sheets": [{"properties": {"title": "Tide", "sheetId": 1}}]}
    with patch.object(gs, "get_sheet"), \
         patch.object(gs, "execute_with_retry", new=AsyncMock(return_value=payload)):
        assert await cog.sheet_list("SS") == payload
```
Note: `_cog()` uses `GoogleSheets.__new__`, bypassing `__init__`, so `self._notify_retry` (called as `execute_with_retry`'s `on_retry` kwarg inside the real `sheet_list`) is never invoked here because `execute_with_retry` itself is mocked out — the mock replaces the whole call, so `on_retry` is never reached. No `_notify_retry` stubbing needed.

- [ ] **Step 17: Fix the 9 `compute_insert_index` tests and the 1 `get_page_name_by_id` test in `tests/test_sheet_ordering.py`**

For each of these 9 test functions — `test_compute_insert_index_no_puzzles_target` (line 49), `test_compute_insert_index_no_puzzles_regular` (58), `test_compute_insert_index_target_after_existing_target` (70), `test_compute_insert_index_target_after_last_of_multiple_targets` (80), `test_compute_insert_index_regular_after_existing_regular` (97), `test_compute_insert_index_regular_after_target_when_no_regulars` (106), `test_compute_insert_index_regular_after_last_regular_not_last_target` (116), `test_compute_insert_index_ignores_solved_puzzles` (136), `test_compute_insert_index_skips_hidden_sheets` (151) — apply the same two-part change: prefix `def test_...` with `async def test_...`, change `patch.object(cog, "sheet_list", return_value=...)` to `patch.object(cog, "sheet_list", new=AsyncMock(return_value=...))`, and add `await` before the `cog.compute_insert_index(...)` call.

Example — `test_compute_insert_index_no_puzzles_target` (lines 49–55), replace:
```python
def test_compute_insert_index_no_puzzles_target(tmp_path):
    cog = make_sheets_cog()
    # Sheet list has only reserved slots (indices 0-6), no puzzle tabs
    reserved = [_sheet_entry(i, i) for i in range(7)]
    with patch.object(cog, "sheet_list", return_value={"sheets": reserved}):
        idx = cog.compute_insert_index(is_target=True, puzzles=[])
    assert idx == cog.INITIAL_OFFSET
```
with:
```python
async def test_compute_insert_index_no_puzzles_target(tmp_path):
    cog = make_sheets_cog()
    # Sheet list has only reserved slots (indices 0-6), no puzzle tabs
    reserved = [_sheet_entry(i, i) for i in range(7)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": reserved})):
        idx = await cog.compute_insert_index(is_target=True, puzzles=[])
    assert idx == cog.INITIAL_OFFSET
```
Apply the identical `def` → `async def`, `return_value=` → `new=AsyncMock(return_value=...)`, `idx = cog....` → `idx = await cog....` transform to the other 8 tests, matching each one's existing `sheets`/`reserved` variable name and assertions (do not change the assertions or fixture data — only the async plumbing).

Then fix `test_restore_puzzle_spreadsheet_moves_additional_sheets_back_when_archived` (line 287, already `async def`) — replace:
```python
        patch.object(cog, "get_page_name_by_id", return_value="Extra Sheet"),
```
with:
```python
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Extra Sheet")),
```

- [ ] **Step 18: Run the file's own tests again — expect pass**

```bash
pipenv run python -m pytest tests/test_sheet_list_guard.py tests/test_sheet_ordering.py -q > /tmp/test.log 2>&1
```
Expected: PASS, all tests green.

- [ ] **Step 19: Commit**

```bash
git add bot/cogs/puzzles_gsheet.py tests/test_sheet_list_guard.py tests/test_sheet_ordering.py
git commit -m "fix(gsheet): make sheet_list and its helpers async (#24, part 1/2)"
```

---

## Task 2: Fix every call site in `bot/core/puzzle_service.py`

**Files:**
- Modify: `bot/core/puzzle_service.py` (5 methods: `_reconcile_tabs`, `reconcile`/`_collect_orphan_tabs`, `remove_minimeta`, `refresh_unassigned`, `unsolve`, `create_mini`, `create_puzzle`)

- [ ] **Step 1: Promote the `_tab_present` nested closure to async in `_reconcile_tabs`**

Replace (around lines 371–390):
```python
    async def _reconcile_tabs(self, hunt, puzzle, *, apply):
        actions = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return actions
        spreadsheet = (gsheet_cog.get_archive_spreadsheet_id() if puzzle.archived
                       else gsheet_cog.get_spreadsheet_id())

        def _tab_present(page_id):
            if not page_id:
                return False
            return gsheet_cog.get_page_name_by_id(page_id, spreadsheet) is not None

        if not _tab_present(puzzle.google_page_id):
            actions.append(ReconcileAction("tab_create", puzzle.name, "main tab missing"))
            if apply:
                existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
                insert_idx = gsheet_cog.compute_insert_index(
                    getattr(puzzle, 'is_target', False), existing)
                await gsheet_cog.add_new_puzzle_sheet(puzzle, insert_idx)
                await gsheet_cog.update_puzzle_info(puzzle)
                PuzzleJsonDb.commit(puzzle)
        return actions
```
with:
```python
    async def _reconcile_tabs(self, hunt, puzzle, *, apply):
        actions = []
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return actions
        spreadsheet = (gsheet_cog.get_archive_spreadsheet_id() if puzzle.archived
                       else gsheet_cog.get_spreadsheet_id())

        async def _tab_present(page_id):
            if not page_id:
                return False
            return await gsheet_cog.get_page_name_by_id(page_id, spreadsheet) is not None

        if not await _tab_present(puzzle.google_page_id):
            actions.append(ReconcileAction("tab_create", puzzle.name, "main tab missing"))
            if apply:
                existing = PuzzleJsonDb.get_all_from_hunt(hunt.id)
                insert_idx = await gsheet_cog.compute_insert_index(
                    getattr(puzzle, 'is_target', False), existing)
                await gsheet_cog.add_new_puzzle_sheet(puzzle, insert_idx)
                await gsheet_cog.update_puzzle_info(puzzle)
                PuzzleJsonDb.commit(puzzle)
        return actions
```
Note: `await gsheet_cog.get_page_name_by_id(...) is not None` — `await` binds tighter than `is`, so this parses as `(await gsheet_cog.get_page_name_by_id(...)) is not None`, which is correct; no extra parens needed, but add them if you prefer explicit clarity.

- [ ] **Step 2: Promote `_collect_orphan_tabs` to async and await it from `reconcile`**

Replace (around line 448, just the signature):
```python
    def _collect_orphan_tabs(self, hunt, puzzles, report):
```
with:
```python
    async def _collect_orphan_tabs(self, hunt, puzzles, report):
```
and inside its body, replace (around line 465):
```python
        try:
            sheets = gsheet_cog.sheet_list(spreadsheet)["sheets"]
        except Exception:  # noqa: BLE001
            logger.exception("reconcile: could not list tabs for orphan scan")
            return
```
with:
```python
        try:
            sheets = (await gsheet_cog.sheet_list(spreadsheet))["sheets"]
        except Exception:  # noqa: BLE001
            logger.exception("reconcile: could not list tabs for orphan scan")
            return
```
Then update the call site in `reconcile` (around line 423):
```python
        self._collect_orphan_channels(guild, rounds, puzzles, report)
        self._collect_orphan_tabs(hunt, puzzles, report)
        return report
```
with:
```python
        self._collect_orphan_channels(guild, rounds, puzzles, report)
        await self._collect_orphan_tabs(hunt, puzzles, report)
        return report
```
(`_collect_orphan_channels` is a separate sync method that never touches Sheets — leave it as-is.)

- [ ] **Step 3: Fix the generator-inside-`any()` site in `remove_minimeta`**

Replace (around lines 1387–1391):
```python
            has_table = any(
                bool(s.get("tables"))
                for s in gsheet_cog.sheet_list(gsheet_cog.get_spreadsheet_id())["sheets"]
                if s["properties"]["sheetId"] == int(puzzle.google_page_id)
            )
```
with:
```python
            sheets = (await gsheet_cog.sheet_list(gsheet_cog.get_spreadsheet_id()))["sheets"]
            has_table = any(
                bool(s.get("tables"))
                for s in sheets
                if s["properties"]["sheetId"] == int(puzzle.google_page_id)
            )
```

- [ ] **Step 4: Fix the `get_page_id_by_name` call in `refresh_unassigned`**

Replace (around line 2069):
```python
                page_id = gsheet_cog.get_page_id_by_name(tab_name)
```
with:
```python
                page_id = await gsheet_cog.get_page_id_by_name(tab_name)
```

- [ ] **Step 5: Fix the 4 `compute_insert_index` call sites**

In `_reconcile_tabs` this was already handled in Step 1 above. The remaining 3:

`unsolve` (around line 1019):
```python
            restore_idx = await gsheet_cog.compute_insert_index(puzzle.is_target, existing)
```

`create_mini` (around line 1539):
```python
            insert_idx = await gsheet_cog.compute_insert_index(False, existing)  # minis are regular
```

`create_puzzle` (around line 1681):
```python
            insert_idx = await gsheet_cog.compute_insert_index(new_puzzle.is_target, existing)
```

- [ ] **Step 6: Grep-verify no remaining unawaited call sites in this file**

```bash
grep -n "gsheet_cog\.\(sheet_list\|get_unique_tab_name\|get_page_id_by_name\|get_page_name_by_id\|move_sheet_to_end\|compute_insert_index\|get_puzzle_sheet_index\)(" bot/core/puzzle_service.py
```
Expected: every match line contains `await` immediately before `gsheet_cog.`. If any doesn't, fix it before continuing.

- [ ] **Step 7: Fix `tests/test_reconcile.py` mocks**

Three fixes needed, all because these tests build `gcog = MagicMock()` and configure `.return_value` on a plain (non-async) mock attribute — once the real methods are `async def`, `await`ing that plain `MagicMock()` call result raises `TypeError: object MagicMock can't be used in 'await' expression'`.

First, the shared `_gcog` factory (lines 56–66) sets `get_page_name_by_id` as a plain `MagicMock`, used by every `_reconcile_tabs` test via the `_tab_present` closure (Task 2 Step 1). Replace:
```python
def _gcog(page_name=None):
    g = MagicMock()
    g.set_spreadsheet_id = MagicMock()
    g.set_archive_spreadsheet_id = MagicMock()
    g.get_spreadsheet_id.return_value = "SS"
    g.get_archive_spreadsheet_id.return_value = "ARC"
    g.INITIAL_OFFSET = 999
    g.get_page_name_by_id = MagicMock(return_value=page_name)
    g.add_new_puzzle_sheet = AsyncMock()
    g.update_puzzle_info = AsyncMock()
    return g
```
with:
```python
def _gcog(page_name=None):
    g = MagicMock()
    g.set_spreadsheet_id = MagicMock()
    g.set_archive_spreadsheet_id = MagicMock()
    g.get_spreadsheet_id.return_value = "SS"
    g.get_archive_spreadsheet_id.return_value = "ARC"
    g.INITIAL_OFFSET = 999
    g.get_page_name_by_id = AsyncMock(return_value=page_name)
    g.add_new_puzzle_sheet = AsyncMock()
    g.update_puzzle_info = AsyncMock()
    g.compute_insert_index = AsyncMock(return_value=999)
    return g
```
(`compute_insert_index` is added because `test_tab_recreated_when_missing_and_written_back`, line ~196, exercises the `apply=True` branch of `_reconcile_tabs`, which now awaits `gsheet_cog.compute_insert_index(...)` — Task 2 Step 1 — and `_gcog` didn't set it before, so it silently returned an un-awaitable auto-`MagicMock`.)

Second, `test_collect_orphan_tabs_skips_structural_tabs` (line 277) builds its own bare `gcog = MagicMock()` and calls `svc._collect_orphan_tabs(...)` directly as a sync call — but Task 2 Step 2 makes `_collect_orphan_tabs` `async def`. Replace:
```python
def test_collect_orphan_tabs_skips_structural_tabs():
    """Structural tabs (REFERENCE template tab, Unassigned Puzzles, Evidence)
    and puzzle additional-sheet tabs are never orphans — only a genuinely
    unmatched tab is flagged."""
    def _sheet(sheet_id, title):
        return {"properties": {"sheetId": sheet_id, "title": title}}

    gcog = MagicMock()
    gcog.get_spreadsheet_id.return_value = "SS"
    gcog.sheet_list.return_value = {"sheets": [
        _sheet(100, "Tide"),               # valid puzzle tab
        _sheet(101, "Tide — notes"),       # puzzle additional sheet
        _sheet(200, "Unassigned Puzzles"), # structural (page-id tracked)
        _sheet(201, "Evidence"),           # structural (page-id tracked)
        _sheet(300, "REFERENCE"),          # structural template tab (untracked)
        _sheet(999, "old-puzzle"),         # genuine orphan
    ]}
    svc = _service_with_gsheet(gcog)
    hunt = make_hunt(id=1, google_sheet_id="SS", archive_google_sheet_id="ARC",
                     unassigned_sheet_page_id=200, evidence_sheet_page_id=201)
    p = _puzzle(google_page_id="100")
    p.additional_sheets = [SimpleNamespace(google_page_id="101")]
    report = ReconcileReport()
    svc._collect_orphan_tabs(hunt, [p], report)
    assert report.orphan_tabs == [(999, "old-puzzle")]
```
with:
```python
async def test_collect_orphan_tabs_skips_structural_tabs():
    """Structural tabs (REFERENCE template tab, Unassigned Puzzles, Evidence)
    and puzzle additional-sheet tabs are never orphans — only a genuinely
    unmatched tab is flagged."""
    def _sheet(sheet_id, title):
        return {"properties": {"sheetId": sheet_id, "title": title}}

    gcog = MagicMock()
    gcog.get_spreadsheet_id.return_value = "SS"
    gcog.sheet_list = AsyncMock(return_value={"sheets": [
        _sheet(100, "Tide"),               # valid puzzle tab
        _sheet(101, "Tide — notes"),       # puzzle additional sheet
        _sheet(200, "Unassigned Puzzles"), # structural (page-id tracked)
        _sheet(201, "Evidence"),           # structural (page-id tracked)
        _sheet(300, "REFERENCE"),          # structural template tab (untracked)
        _sheet(999, "old-puzzle"),         # genuine orphan
    ]})
    svc = _service_with_gsheet(gcog)
    hunt = make_hunt(id=1, google_sheet_id="SS", archive_google_sheet_id="ARC",
                     unassigned_sheet_page_id=200, evidence_sheet_page_id=201)
    p = _puzzle(google_page_id="100")
    p.additional_sheets = [SimpleNamespace(google_page_id="101")]
    report = ReconcileReport()
    await svc._collect_orphan_tabs(hunt, [p], report)
    assert report.orphan_tabs == [(999, "old-puzzle")]
```

- [ ] **Step 8: Fix `tests/test_minimeta_service.py` mocks**

`test_commits_after_sheet_restore` (around line 396) sets `compute_insert_index` as a plain `MagicMock`, consumed via `asyncio.run(svc.unsolve(...))` which now awaits it (Task 2 Step 5). Replace:
```python
        gsheet_cog = MagicMock()
        gsheet_cog.compute_insert_index.return_value = 3
```
with:
```python
        gsheet_cog = MagicMock()
        gsheet_cog.compute_insert_index = AsyncMock(return_value=3)
```

`test_demotes_tab_when_rollup_table_present` (around line 624) and `test_skips_demote_when_no_table` (around line 653) each set `gsheet.sheet_list.return_value = {...}`, consumed by `remove_minimeta`'s now-awaited `sheet_list` call (Task 2 Step 3). In each, replace:
```python
        gsheet.sheet_list.return_value = {
            "sheets": [{"properties": {"sheetId": 99}, "tables": [{"id": "t"}]}]
        }
```
with:
```python
        gsheet.sheet_list = AsyncMock(return_value={
            "sheets": [{"properties": {"sheetId": 99}, "tables": [{"id": "t"}]}]
        })
```
and (in the second test) replace:
```python
        gsheet.sheet_list.return_value = {
            "sheets": [{"properties": {"sheetId": 99}}]
        }
```
with:
```python
        gsheet.sheet_list = AsyncMock(return_value={
            "sheets": [{"properties": {"sheetId": 99}}]
        })
```
Both enclosing test methods are already `async def` (confirm with `grep -n "async def test_demotes_tab_when_rollup_table_present\|async def test_skips_demote_when_no_table" tests/test_minimeta_service.py`) — no signature change needed, only the mock construction.

- [ ] **Step 9: Fix `tests/test_aggregate_to_tab.py` mocks**

`_capture_values` (around line 199) assigns a plain sync lambda to `cog.get_page_name_by_id`, and `test_missing_tab_raises` (around line 244) does the same — both consumed by `aggregate_to_tab`'s now-awaited call (Task 1 Step 13). Python lambdas can't be `async`, so replace with a small async function. Replace:
```python
    def _capture_values(self, cog, tab_name="My Tab"):
        cog.get_page_name_by_id = lambda page_id, spreadsheet_id=None: tab_name
        recorded = {}
```
with:
```python
    def _capture_values(self, cog, tab_name="My Tab"):
        async def fake_get_page_name_by_id(page_id, spreadsheet_id=None):
            return tab_name
        cog.get_page_name_by_id = fake_get_page_name_by_id
        recorded = {}
```
and replace:
```python
    def test_missing_tab_raises(self):
        cog = _cog()
        cog.get_page_name_by_id = lambda page_id, spreadsheet_id=None: None
        with pytest.raises(RuntimeError):
            asyncio.run(cog.aggregate_to_tab("S", 999, ["x"], []))
```
with:
```python
    def test_missing_tab_raises(self):
        cog = _cog()
        async def fake_get_page_name_by_id(page_id, spreadsheet_id=None):
            return None
        cog.get_page_name_by_id = fake_get_page_name_by_id
        with pytest.raises(RuntimeError):
            asyncio.run(cog.aggregate_to_tab("S", 999, ["x"], []))
```

- [ ] **Step 10: Run all affected puzzle_service-adjacent tests**

```bash
timeout 120 pipenv run python -m pytest tests/test_reconcile.py tests/test_minimeta_service.py tests/test_aggregate_to_tab.py tests/test_unassigned_service.py -q > /tmp/test.log 2>&1
```
Expected: PASS. If any other test in these files (or a file not covered above) fails with `TypeError: object MagicMock can't be used in 'await' expression` or `TypeError: object <type> can't be used in 'await' expression`, it's another missed mock of one of the 7 converted methods — apply the same `AsyncMock`/`async def` fix pattern used above.

- [ ] **Step 11: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_reconcile.py tests/test_minimeta_service.py tests/test_aggregate_to_tab.py
git commit -m "fix(gsheet): await sheet_list-derived calls in puzzle_service (#24, part 2/2)"
```

---

## Task 3: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Repo-wide grep for any remaining unawaited call to the 7 converted methods**

```bash
grep -rn "\.\(sheet_list\|get_unique_tab_name\|get_page_id_by_name\|get_page_name_by_id\|move_sheet_to_end\|compute_insert_index\|get_puzzle_sheet_index\)(" bot/ tests/ | grep -v "^tests/test_sheet_ordering.py\|^tests/test_sheet_list_guard.py"
```
Inspect every result. Each production-code match must have `await` immediately before the method call (or be inside a `patch.object(...)` mock target string, which is fine — mock target strings aren't call sites). If a call site was missed, fix it and re-run this grep before moving on.

- [ ] **Step 2: Run the full test suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
Expected: PASS, exit code 0. If it times out (exit 124), isolate per-file with `timeout 60 pipenv run python -m pytest tests/<file>.py -q` to find which file hangs — a hang here most likely means a test that isn't `async def` is calling one of the 7 now-async methods directly and getting a coroutine object it never awaits (no crash, just a silently-skipped assertion or an unawaited-coroutine `RuntimeWarning` — check `/tmp/test.log` for `RuntimeWarning: coroutine '...' was never awaited`).

- [ ] **Step 3: Update the review doc to mark #24 as fixed**

Read `docs/reviews/2026-07-02-staging-full-review.md` around lines 244–259 (the `### 24.` finding) and its status header, then edit similarly to how tranches 1–4 were annotated: change `**Status: DEFERRED** ...` to `**Status: FIXED** (2026-07-02, branch working-1, commits <hash1> <hash2>).`, and add a line to the "Implementation status, second pass" list at the top of the doc noting a tranche/entry for #24 with the actual commit hashes from Task 1 Step 19 and Task 2 Step 11.

- [ ] **Step 4: Commit the doc update**

```bash
git add docs/reviews/2026-07-02-staging-full-review.md
git commit -m "docs(review): annotate #24 (gsheet async conversion) as fixed"
```

---

## Verification

- All three tasks' test runs (Task 1 Step 18, Task 2 Step 7, Task 3 Step 2) pass.
- The Task 3 Step 1 grep returns no unawaited production call sites.
- `docs/reviews/2026-07-02-staging-full-review.md` finding #24 is marked fixed with real commit hashes.

## Rollback / recovery notes

Each task is its own commit; if Task 2 or 3 surfaces a problem, `git revert` the specific commit rather than hand-unwinding — the three commits are independently revertable since Task 1 alone (converting the 7 methods + their in-file callers) is self-consistent, and Task 2 only adds `await`s in a different file without changing Task 1's method signatures further.

"""Request-shape tests for the feeder matrix tab (Plan 2, Task 4).

No live Google: every test here patches the cog's batch/low-level seams and
asserts the SHAPE of the requests the cog would send. None of this proves what
Google does with those requests — addTable's columnType handling, checkbox
rendering, and the read-back format were verified separately, once, by a
one-off live probe (scripts/diagnose_feeder_matrix.py; findings recorded in
docs/superpowers/plans/2026-08-12-feeder-pool-matrix.md). Mirrors
tests/test_contribution_table_requests.py's own honesty about this split.
"""
import asyncio

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.feeder_pool import matrix_header
from bot.core.sheet_tables import feeder_matrix_table_name
from bot.store.puzzle_data import PuzzleData


def _cog():
    return GoogleSheets.__new__(GoogleSheets)


def _capture_batch(cog):
    calls = []

    async def fake(body, archive=False):
        calls.append(body)
        return {"replies": [{"addTable": {"table": {"tableId": "TID"}}}]}

    cog.batch_update = fake
    return calls


def _puzzle(id, name, **kw):
    p = PuzzleData()
    p.id = id
    p.name = name
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _minimeta(feeder_tab_page_id=None):
    return _puzzle(100, "Matching Meta", feeder_tab_page_id=feeder_tab_page_id)


def _minis():
    return [_puzzle(101, "Mini 1"), _puzzle(102, "Mini 2")]


# --- Task 4: ensure_feeder_matrix_tab ---------------------------------------

def _run_ensure(minimeta, minis, existing_sheets=None):
    cog = _cog()
    calls = _capture_batch(cog)
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_unique(name):
        return name
    cog.get_unique_tab_name = _fake_unique

    async def _fake_create_blank_tab(spreadsheet_id, name):
        return 500
    cog.create_blank_tab = _fake_create_blank_tab

    updates = []

    async def _fake_values_update(spreadsheet_id, a1, values):
        updates.append((a1, values))
    cog._values_update = _fake_values_update

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": existing_sheets or []}
    cog.sheet_list = _fake_sheet_list

    page_id = asyncio.run(cog.ensure_feeder_matrix_tab(minimeta, minis))
    return page_id, calls, updates


def test_ensure_feeder_matrix_tab_addtable_spans_every_column_and_types_ticks():
    minimeta = _minimeta()
    minis = _minis()
    page_id, calls, updates = _run_ensure(minimeta, minis)

    assert page_id == 500
    header = matrix_header(minimeta, minis)
    assert updates and updates[0][1] == [header]   # header written plainly too

    add = next(r["addTable"]["table"] for body in calls
               for r in body["requests"] if "addTable" in r)
    assert add["name"] == feeder_matrix_table_name(500)
    assert add["range"]["sheetId"] == 500
    assert add["range"]["startRowIndex"] == 0 and add["range"]["endRowIndex"] == 1
    assert add["range"]["startColumnIndex"] == 0
    # Spans every column (bot-owned + note + every tick), not just the 3 bot ones.
    assert add["range"]["endColumnIndex"] == len(header) == 7

    props = {p["columnIndex"]: p for p in add["columnProperties"]}
    # key, item, Len, note carry no columnType.
    for i in range(4):
        assert "columnType" not in props[i]
    # main + every mini's tick column is BOOLEAN.
    for i in range(4, len(header)):
        assert props[i]["columnType"] == "BOOLEAN"
    # main is the first tick column.
    assert header[4].startswith("main")
    assert props[4]["columnName"] == header[4]


def test_ensure_feeder_matrix_tab_idempotent_when_tab_still_exists():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    page_id, calls, updates = _run_ensure(
        minimeta, minis, existing_sheets=[{"properties": {"sheetId": 500}}])

    assert page_id == 500
    assert calls == []      # no addTable / any batchUpdate at all
    assert updates == []    # no header rewrite either


def test_ensure_feeder_matrix_tab_recreates_when_the_old_tab_is_gone():
    # feeder_tab_page_id is stale (the tab was deleted) -- must not silently
    # return the dead id, or every render would target a tab that no longer
    # exists.
    minimeta = _minimeta(feeder_tab_page_id=999)
    minis = _minis()
    page_id, calls, _updates = _run_ensure(
        minimeta, minis, existing_sheets=[{"properties": {"sheetId": 111}}])

    assert page_id == 500   # the freshly created tab, not the stale 999
    assert any("addTable" in r for body in calls for r in body["requests"])

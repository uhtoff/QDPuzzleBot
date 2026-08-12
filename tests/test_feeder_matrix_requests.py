"""Request-shape tests for the feeder matrix tab (Plan 2, Tasks 4-5).

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
from bot.core.feeder_pool import matrix_header, matrix_rows
from bot.core.sheet_tables import feeder_matrix_table_name
from bot.store.feeder_pool_data import FeederClaimData, FeederPoolItemData
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


# --- Task 5: render_feeder_matrix -------------------------------------------

def _table(end_col):
    return {"tableId": "T", "name": feeder_matrix_table_name(500),
            "range": {"startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": end_col}}


def _run_render(minimeta, items, minis, claims, existing_sheets):
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": existing_sheets}
    cog.sheet_list = _fake_sheet_list

    updates = []

    async def _fake_values_update(spreadsheet_id, a1, values):
        updates.append((a1, values))
    cog._values_update = _fake_values_update

    batch_calls = []

    async def _fake_batch(body, archive=False):
        batch_calls.append(body)
    cog.batch_update = _fake_batch

    values_get_calls = []

    async def _fake_values_get(spreadsheet_id, a1_range):
        values_get_calls.append(a1_range)
        return {"values": [["key"]] + [[f"f:{i.id}"] for i in items]}
    cog._values_get = _fake_values_get

    reconcile_calls = []

    async def _fake_reconcile(**kwargs):
        reconcile_calls.append(kwargs)
    cog.reconcile_table = _fake_reconcile

    asyncio.run(cog.render_feeder_matrix(minimeta, items, minis, claims))
    return updates, batch_calls, values_get_calls, reconcile_calls


def test_render_feeder_matrix_reconciles_bot_owned_columns_only():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    items = [FeederPoolItemData(id=12, text="ALBATROSS"),
             FeederPoolItemData(id=13, text="MARZIPAN")]
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    _updates, _batch, _get, reconcile_calls = _run_render(minimeta, items, minis, [], sheets)

    assert len(reconcile_calls) == 1
    call = reconcile_calls[0]
    assert call["page_id"] == 500
    assert call["data_col_count"] == 3        # key/item/Len only -- note+ticks are the solver's
    assert call["numeric_cols"] == (2,)
    assert call["grey_below"] is True
    assert call["table_name"] == feeder_matrix_table_name(500)
    assert call["desired_rows"] == matrix_rows(items)


def test_render_feeder_matrix_rewrites_the_header_row_every_time():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    updates, batch_calls, _get, _reconcile = _run_render(minimeta, [], minis, [], sheets)

    header = matrix_header(minimeta, minis)
    assert updates and updates[0] == ("'Feeders'!A1", [header])
    # Table is already wide enough (7 cols) -- no widen request needed.
    assert not any("updateTable" in r for body in batch_calls for r in body["requests"])


def test_render_feeder_matrix_widens_the_table_for_a_new_mini_column():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()   # header needs 7 columns
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(6)]}]   # table is one column too narrow

    _updates, batch_calls, _get, _reconcile = _run_render(minimeta, [], minis, [], sheets)

    widen = next(r["updateTable"] for body in batch_calls
                 for r in body["requests"] if "updateTable" in r)
    assert widen["table"]["tableId"] == "T"
    assert widen["table"]["range"]["endColumnIndex"] == len(matrix_header(minimeta, minis)) == 7
    assert widen["fields"] == "range"


def test_render_feeder_matrix_writes_true_for_a_confirmed_claim():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    items = [FeederPoolItemData(id=12, text="ALBATROSS"),
             FeederPoolItemData(id=13, text="MARZIPAN")]
    claims = [FeederClaimData(id=1, item_id=13, target_id=101, confirmed=True)]   # Mini 1 x MARZIPAN
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    _updates, batch_calls, get_calls, _reconcile = _run_render(minimeta, items, minis, claims, sheets)

    assert get_calls == ["'Feeders'!A:A"]
    write = next(r["updateCells"] for body in batch_calls for r in body["requests"]
                 if "updateCells" in r
                 and r["updateCells"]["rows"][0]["values"][0]
                     .get("userEnteredValue", {}).get("boolValue") is True)
    # f:13 (MARZIPAN) is the fake key column's row 2 (header=0, f:12=1, f:13=2).
    assert write["range"]["startRowIndex"] == 2
    header = matrix_header(minimeta, minis)
    mini1_col = next(i for i, label in enumerate(header) if label.startswith("Mini 1"))
    assert write["range"]["startColumnIndex"] == mini1_col


def test_render_feeder_matrix_leaves_an_unconfirmed_claim_untouched():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    items = [FeederPoolItemData(id=12, text="ALBATROSS")]
    claims = [FeederClaimData(id=1, item_id=12, target_id=101, confirmed=False)]
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    _updates, batch_calls, get_calls, _reconcile = _run_render(minimeta, items, minis, claims, sheets)

    assert get_calls == []   # no need to even read the key column back
    assert not any("updateCells" in r for body in batch_calls for r in body["requests"])


def test_render_feeder_matrix_is_a_noop_without_a_tab():
    minimeta = _minimeta(feeder_tab_page_id=None)
    updates, batch_calls, get_calls, reconcile_calls = _run_render(minimeta, [], [], [], [])
    assert updates == [] and batch_calls == [] and get_calls == [] and reconcile_calls == []

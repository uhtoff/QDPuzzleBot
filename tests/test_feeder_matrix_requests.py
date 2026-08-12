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
from bot.core.feeder_pool import (
    REMOVED_COLUMN, matrix_header, matrix_rows, parse_ticks)
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


def _run_render(minimeta, items, minis, claims, existing_sheets, existing_header=None):
    """`existing_header` is the header row as it currently reads back off the tab
    -- the record of which column belongs to which target. None means an empty
    row 1 (a tab written for the first time)."""
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
        if a1_range.endswith("!1:1"):
            return {"values": [existing_header]} if existing_header else {}
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


def test_render_types_a_column_the_widen_adds_as_a_checkbox():
    """Probed live 2026-08-12 (scripts/diagnose_feeder_matrix.py, Q5): a column
    the Table GAINS via updateTable {"fields": "range"} comes back with NO
    columnType, so a mini added after the tab was created would get a column with
    no checkbox in it. Re-sending columnProperties under the wider mask fixes it
    (Q5b). Note the row-growth case is different and does survive (Q2)."""
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(6)]}]

    _updates, batch_calls, _get, _reconcile = _run_render(minimeta, [], minis, [], sheets)

    widen = next(r["updateTable"] for body in batch_calls
                 for r in body["requests"] if "updateTable" in r)
    assert widen["fields"] == "range,columnProperties"
    props = {p["columnIndex"]: p for p in widen["table"]["columnProperties"]}
    header = matrix_header(minimeta, minis)
    assert len(props) == len(header) == 7
    for i in range(4):                      # key, item, Len, note
        assert "columnType" not in props[i]
    for i in range(4, 7):                   # main + both minis, incl. the new one
        assert props[i]["columnType"] == "BOOLEAN"
        assert props[i]["columnName"] == header[i]


def test_render_never_sends_an_empty_column_name_with_the_widen():
    """Probed live (Q5c): updateTable rejects columnProperties carrying an empty
    columnName -- "Table header row cell must have a value" -- and Sheets renames
    an emptied header cell to "Column N" anyway (Q6a). So a departed target's
    column is labelled, not left blank."""
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = [_puzzle(103, "Mini 3")]        # #101 and #102 are both gone
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    updates, batch_calls, _get, _reconcile = _run_render(
        minimeta, [], minis, [], sheets, existing_header=HEAD_2)

    header = _written_header(updates)
    assert all(label for label in header), header
    assert header[5] == header[6] == REMOVED_COLUMN
    widen = next(r["updateTable"] for body in batch_calls
                 for r in body["requests"] if "updateTable" in r)
    assert all(p["columnName"] for p in widen["table"]["columnProperties"])


def test_render_feeder_matrix_writes_true_for_a_confirmed_claim():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = _minis()
    items = [FeederPoolItemData(id=12, text="ALBATROSS"),
             FeederPoolItemData(id=13, text="MARZIPAN")]
    claims = [FeederClaimData(id=1, item_id=13, target_id=101, confirmed=True)]   # Mini 1 x MARZIPAN
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    _updates, batch_calls, get_calls, _reconcile = _run_render(minimeta, items, minis, claims, sheets)

    assert get_calls == ["'Feeders'!1:1", "'Feeders'!A:A"]
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

    # No need to read the key column back -- only the header row is read.
    assert "'Feeders'!A:A" not in get_calls
    assert not any("updateCells" in r for body in batch_calls for r in body["requests"])


def test_render_feeder_matrix_is_a_noop_without_a_tab():
    minimeta = _minimeta(feeder_tab_page_id=None)
    updates, batch_calls, get_calls, reconcile_calls = _run_render(minimeta, [], [], [], [])
    assert updates == [] and batch_calls == [] and get_calls == [] and reconcile_calls == []


# --- Column stability when a mini is deleted --------------------------------
#
# values.update writes exactly as many cells as the data has and never truncates,
# so a header rebuilt from the CURRENT mini list re-packs every label one column
# to the left of a deletion while the tick data underneath stays put. Each case
# uses at least two minis so that a re-packing implementation disagrees.

HEAD_2 = ["key", "item", "Len", "note", "main #100", "Mini 1 #101", "Mini 2 #102"]


def _written_header(updates):
    return next(values[0] for a1, values in updates if a1.endswith("!A1"))


def test_render_keeps_the_surviving_minis_column_when_the_first_is_deleted():
    minimeta = _minimeta(feeder_tab_page_id=500)
    survivor = _puzzle(102, "Mini 2")          # Mini 1 (#101) has been deleted
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    updates, batch_calls, _get, _reconcile = _run_render(
        minimeta, [], [survivor], [], sheets, existing_header=HEAD_2)

    header = _written_header(updates)
    assert header[6] == "Mini 2 #102"    # unmoved
    assert header[5] == REMOVED_COLUMN   # the deleted mini's column, retired
    # ... and its data cells are cleared, so no tick outlives its target.
    clear = next(r["updateCells"] for body in batch_calls
                 for r in body["requests"] if "updateCells" in r
                 and "rows" not in r["updateCells"])
    assert clear["range"]["startColumnIndex"] == 5
    assert clear["range"]["endColumnIndex"] == 6
    assert clear["range"]["startRowIndex"] == 1      # header row kept
    assert "endRowIndex" not in clear["range"]       # to the bottom of the tab
    assert clear["fields"] == "userEnteredValue"


def test_the_surviving_mini_does_not_inherit_the_deleted_ones_ticks():
    """The whole point: read the header the render just wrote back over the tick
    data, which did NOT move, and nothing of Mini 1's lands on Mini 2."""
    minimeta = _minimeta(feeder_tab_page_id=500)
    survivor = _puzzle(102, "Mini 2")
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    updates, _batch, _get, _reconcile = _run_render(
        minimeta, [], [survivor], [], sheets, existing_header=HEAD_2)

    grid = [
        ["f:12", "ALBATROSS", "9", "", "", "TRUE", ""],   # was Mini 1's tick
        ["f:13", "MARZIPAN", "8", "", "", "", "TRUE"],    # is Mini 2's tick
    ]
    ticks = parse_ticks(_written_header(updates), grid, {100, 102})
    assert ticks == {100: [], 102: [13]}


def test_render_keeps_both_neighbours_when_the_middle_mini_is_deleted():
    minimeta = _minimeta(feeder_tab_page_id=500)
    minis = [_puzzle(101, "Mini 1"), _puzzle(103, "Mini 3")]   # #102 deleted
    existing = HEAD_2 + ["Mini 3 #103"]
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(8)]}]

    updates, _batch, _get, _reconcile = _run_render(
        minimeta, [], minis, [], sheets, existing_header=existing)

    header = _written_header(updates)
    assert header == ["key", "item", "Len", "note",
                      "main #100", "Mini 1 #101", REMOVED_COLUMN, "Mini 3 #103"]


def test_render_pads_the_header_write_out_over_a_stale_label():
    """values.update never truncates, so a header shorter than the Table would
    leave the old rightmost label in place -- a second column for a target that
    already has one, which parse_ticks would then union. The pad cells land
    INSIDE the Table, so they are named rather than emptied (probe Q5c/Q6a)."""
    minimeta = _minimeta(feeder_tab_page_id=500)
    survivor = _puzzle(102, "Mini 2")
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(9)]}]      # Table is wider than the header

    updates, _batch, _get, _reconcile = _run_render(
        minimeta, [], [survivor], [], sheets, existing_header=HEAD_2)

    header = _written_header(updates)
    assert len(header) == 9
    assert header[7:] == [REMOVED_COLUMN, REMOVED_COLUMN]
    assert "Mini 1 #101" not in header      # the stale label is gone


def test_render_writes_a_confirmed_tick_into_the_stable_column():
    """render's own write side reads the same rebuilt header, so a re-packing
    implementation puts Mini 2's confirmed tick over Mini 1's leftovers."""
    minimeta = _minimeta(feeder_tab_page_id=500)
    survivor = _puzzle(102, "Mini 2")
    items = [FeederPoolItemData(id=12, text="ALBATROSS"),
             FeederPoolItemData(id=13, text="MARZIPAN")]
    claims = [FeederClaimData(id=1, item_id=13, target_id=102, confirmed=True)]
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"},
               "tables": [_table(7)]}]

    _updates, batch_calls, _get, _reconcile = _run_render(
        minimeta, items, [survivor], claims, sheets, existing_header=HEAD_2)

    write = next(r["updateCells"] for body in batch_calls for r in body["requests"]
                 if "updateCells" in r
                 and r["updateCells"].get("rows"))
    assert write["range"]["startColumnIndex"] == 6      # not 5
    assert write["range"]["startRowIndex"] == 2         # f:13


# --- Task 6: read_feeder_matrix ---------------------------------------------

def _run_read(page_id, sheets, values_response):
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": sheets}
    cog.sheet_list = _fake_sheet_list

    get_calls = []

    async def _fake_values_get(spreadsheet_id, a1_range):
        get_calls.append(a1_range)
        return values_response
    cog._values_get = _fake_values_get

    header, grid = asyncio.run(cog.read_feeder_matrix(page_id))
    return header, grid, get_calls


def test_read_feeder_matrix_is_one_read_of_the_whole_tab():
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders — Meta"}}]
    values_response = {"values": [
        ["key", "item", "Len", "note", "main #1"],
        ["f:12", "ALBATROSS", "9", "", "TRUE"],
    ]}
    header, grid, get_calls = _run_read(500, sheets, values_response)

    assert get_calls == ["'Feeders — Meta'"]   # exactly one read, no A1 range slicing
    assert header == ["key", "item", "Len", "note", "main #1"]
    assert grid == [["f:12", "ALBATROSS", "9", "", "TRUE"]]


def test_read_feeder_matrix_handles_a_missing_values_key():
    """The API omits `values` entirely on an empty range -- every existing
    caller writes `resp.get("values") or []`; this must too."""
    sheets = [{"properties": {"sheetId": 500, "title": "Feeders"}}]
    header, grid, _get = _run_read(500, sheets, {})
    assert header == [] and grid == []


def test_read_feeder_matrix_returns_empty_when_the_tab_is_gone():
    header, grid, get_calls = _run_read(500, [], {"values": [["key"]]})
    assert header == [] and grid == []
    assert get_calls == []   # never even issues the values().get

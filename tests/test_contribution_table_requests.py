"""Request-shape tests for the contribution table + shared reconciler (Milestone D).

Tasks 6-11 of docs/superpowers/plans/2026-06-25-milestone-d-sheet-generation.md.
No live Google: patch the cog's batch/low-level seams and assert the batchUpdate
request bodies. Mirrors tests/test_aggregate_to_tab.py (asyncio.run +
GoogleSheets.__new__). Helpers are file-local. Authored test-first (Opus);
implementation lands separately.
"""
import asyncio
from unittest.mock import MagicMock, patch

import bot.cogs.puzzles_gsheet as gsheet_mod
from bot.cogs.puzzles_gsheet import GoogleSheets
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


def _capture_lowlevel(cog, current_col_a):
    """Patch the low-level seams reconcile_table uses; record batch requests."""
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.sheet_list = lambda spreadsheet_id=None: {
        "sheets": [{"properties": {"sheetId": 900, "title": "Meta", "index": 0}}]}

    async def fake_values_get(spreadsheet_id, a1_range):
        # header + current keys down column A
        return {"values": [["key"]] + [[k] for k in current_col_a]}

    sent = []

    async def fake_batch(body, archive=False):
        sent.append(body)

    cog._values_get = fake_values_get
    cog.batch_update = fake_batch
    return sent


# --- Task 6: _add_contribution_table ---------------------------------------

def test_add_contribution_table_covers_header_and_hides_key_col():
    cog = _cog()
    calls = _capture_batch(cog)

    table_id = asyncio.run(cog._add_contribution_table(page_id=900))

    assert table_id == "TID"
    add = calls[0]["requests"][0]["addTable"]["table"]
    assert add["range"]["sheetId"] == 900
    # 4 columns A-D
    assert add["range"]["startColumnIndex"] == 0
    assert add["range"]["endColumnIndex"] == 4
    cols = [c["columnName"] for c in add["columnProperties"]]
    assert cols == ["key", "Type", "Source", "Content"]
    # A hidden-column request is issued (key column hidden)
    hide = [r for body in calls for r in body["requests"]
            if "updateDimensionProperties" in r]
    assert hide and hide[0]["updateDimensionProperties"]["properties"]["hiddenByUser"] is True


# --- Task 7: reconcile_table (append / update / prune by column-A key) ------

def test_reconcile_table_appends_updates_prunes():
    cog = _cog()
    # current sheet has feeds:2 (will update) and informs:9 (will prune)
    sent = _capture_lowlevel(cog, ["feeds:2", "informs:9"])
    desired = [["feeds:2", "Feeder", "Alpha", "WORD"],   # update
               ["none:3", "Group", "Beta", ""]]          # append
    asyncio.run(cog.reconcile_table(page_id=900, desired_rows=desired,
                                    data_col_count=4))
    flat = [r for body in sent for r in body["requests"]]
    kinds = {k for r in flat for k in r}
    assert "updateCells" in kinds      # feeds:2 row rewritten
    assert "deleteDimension" in kinds  # informs:9 pruned
    # append present (an updateCells writing the new bottom row, or appendCells)
    assert any("updateCells" in r or "appendCells" in r for r in flat)


def test_reconcile_table_numeric_length_and_resizes_table():
    """Number-typed Length column is written numeric (blank when unsolved), and the
    native Table is resized to cover header + data so rows belong to it."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.sheet_list = lambda spreadsheet_id=None: {"sheets": [{
        "properties": {"sheetId": 700, "title": "Unassigned Feeders", "index": 0},
        "tables": [{"tableId": "UT", "range": {
            "sheetId": 700, "startRowIndex": 0, "startColumnIndex": 0,
            "endRowIndex": 1, "endColumnIndex": 5}}],
    }]}

    async def fake_values_get(spreadsheet_id, a1_range):
        return {"values": [["id"]]}   # header only, no data yet

    sent = []

    async def fake_batch(body, archive=False):
        sent.append(body)

    cog._values_get = fake_values_get
    cog.batch_update = fake_batch

    desired = [[1, "Solved", "CAT", 3], [2, "Open", "", ""]]
    asyncio.run(cog.reconcile_table(page_id=700, desired_rows=desired,
                                    data_col_count=4, numeric_cols=(3,)))
    flat = [r for body in sent for r in body["requests"]]
    append = [r for r in flat if "updateCells" in r][-1]["updateCells"]["rows"]
    assert append[0]["values"][3] == {"userEnteredValue": {"numberValue": 3}}
    assert append[1]["values"][3] == {}   # blank, not "", so the batch isn't rejected
    resize = [r["updateTable"] for r in flat if "updateTable" in r]
    assert resize and resize[0]["table"]["range"]["endRowIndex"] == 3
    # Preserve the Table's 5-col span (incl. solver-owned Notes), not the 4 written.
    assert resize[0]["table"]["range"]["endColumnIndex"] == 5


# --- Task 8: generate_puzzle_tab (no template duplicateSheet) ---------------

def test_generate_puzzle_tab_writes_header_and_no_table_for_feeder():
    cog = _cog()
    calls = _capture_batch(cog)
    cog.create_blank_tab = lambda spreadsheet_id, name: 901
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_unique_tab_name = lambda n: n

    p = PuzzleData()
    p.id = 2
    p.name = "Alpha"
    p.url = "http://x"
    p.is_target = False

    page_id = asyncio.run(cog.generate_puzzle_tab(p, is_target=False))
    assert page_id == 901
    # No addTable for a non-target tab
    assert not any("addTable" in r for body in calls for r in body["requests"])


def test_generate_puzzle_tab_adds_contribution_table_for_target():
    cog = _cog()
    calls = _capture_batch(cog)
    cog.create_blank_tab = lambda spreadsheet_id, name: 902
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_unique_tab_name = lambda n: n

    p = PuzzleData()
    p.id = 1
    p.name = "Meta"
    p.url = "http://m"
    p.is_target = True

    asyncio.run(cog.generate_puzzle_tab(p, is_target=True))
    reqs = [r for body in calls for r in body["requests"]]
    assert any("addTable" in r for r in reqs)

    # Regression: the contribution Table must NOT be inserted over a merged range
    # (live Sheets rejects that with "can't insert a table over a merged range").
    def _overlaps(a, b):
        return (a["startRowIndex"] < b["endRowIndex"] and b["startRowIndex"] < a["endRowIndex"]
                and a["startColumnIndex"] < b["endColumnIndex"] and b["startColumnIndex"] < a["endColumnIndex"])

    table_range = next(r["addTable"]["table"]["range"] for r in reqs if "addTable" in r)
    merges = [r["mergeCells"]["range"] for r in reqs if "mergeCells" in r]
    assert merges, "target header should still have merges"
    assert not any(_overlaps(table_range, m) for m in merges)
    # Table sits below the 3 frozen header rows.
    assert table_range["startRowIndex"] == 3


def test_generate_puzzle_tab_formats_tab():
    cog = _cog()
    calls = _capture_batch(cog)
    cog.create_blank_tab = lambda spreadsheet_id, name: 903
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_unique_tab_name = lambda n: n

    p = PuzzleData()
    p.id = 3
    p.name = "Feeder"
    p.url = "http://f"
    p.is_target = False

    asyncio.run(cog.generate_puzzle_tab(p, is_target=False))
    reqs = [r for body in calls for r in body["requests"]]

    frozen = [r["updateSheetProperties"] for r in reqs if "updateSheetProperties" in r]
    assert frozen and frozen[0]["properties"]["gridProperties"]["frozenRowCount"] == 3

    fonts = [r["repeatCell"] for r in reqs if "repeatCell" in r
             and "textFormat" in r["repeatCell"]["cell"]["userEnteredFormat"]]
    assert fonts and fonts[0]["cell"]["userEnteredFormat"]["textFormat"]["fontFamily"] == "JetBrains Mono"

    greys = [r["repeatCell"] for r in reqs if "repeatCell" in r
             and "backgroundColor" in r["repeatCell"]["cell"]["userEnteredFormat"]]
    assert greys and greys[0]["range"]["endRowIndex"] == 3   # the 3 frozen header rows

    widths = [r["updateDimensionProperties"] for r in reqs if "updateDimensionProperties" in r]
    assert widths and widths[0]["range"]["startIndex"] == 0   # col A wide for a feeder


def test_solution_bg_request_targets_shifted_solution_cell():
    cog = _cog()
    cog.sheet_list = lambda spreadsheet_id=None: {"sheets": [{
        "properties": {"sheetId": 905, "title": "Meta"},
        "tables": [{"tableId": "T", "range": {"startRowIndex": 3, "startColumnIndex": 0}}]}]}

    p = PuzzleData()
    p.id = 1
    p.is_target = True
    p.google_page_id = 905

    req = cog._solution_bg_request(p, cog.SOLUTION_GREEN)
    rng = req["repeatCell"]["range"]
    # Solution cell I1 (col 8) shifted +1 for a target -> J1 (col 9).
    assert rng["startColumnIndex"] == 9
    assert req["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"] == cog.SOLUTION_GREEN


# --- Task 9: promote_tab_to_target (insert before table; preserve grid) -----

def test_promote_inserts_columns_before_adding_table():
    cog = _cog()
    calls = _capture_batch(cog)
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def fake_values_get(spreadsheet_id, a1_range):
        return {"values": [["flavour"], ["progress"]]}

    cog._values_get = fake_values_get
    cog.get_page_name_by_id = lambda page_id, spreadsheet_id=None: "Alpha"

    p = PuzzleData()
    p.id = 1
    p.name = "Alpha"
    p.url = "http://a"
    p.google_page_id = 905

    asyncio.run(cog.promote_tab_to_target(p))

    flat = [(i, k) for i, body in enumerate(calls)
            for r in body["requests"] for k in r]
    # an insertDimension COLUMNS happens before any addTable
    insert_idx = min(i for i, k in flat if k == "insertDimension")
    table_idx = min((i for i, k in flat if k == "addTable"), default=10**9)
    assert insert_idx < table_idx


def test_demote_removes_contribution_columns():
    # Inverse of promote: drop the CONTRIBUTION_WIDTH left columns (and the Table
    # they hold); the solver grid slides back into place.
    cog = _cog()
    calls = _capture_batch(cog)

    async def fake_update_info(puzzle, update_tab_name=False, name=None, page_id=None):
        pass

    cog.update_puzzle_info = fake_update_info

    p = PuzzleData()
    p.id = 1
    p.name = "Alpha"
    p.google_page_id = 905

    asyncio.run(cog.demote_tab_to_puzzle(p))

    deletes = [r["deleteDimension"] for body in calls for r in body["requests"]
               if "deleteDimension" in r]
    assert deletes, "demote must delete the contribution columns"
    rng = deletes[0]["range"]
    assert rng["sheetId"] == 905
    assert rng["dimension"] == "COLUMNS"
    assert rng["startIndex"] == 0
    assert rng["endIndex"] == cog.CONTRIBUTION_WIDTH


# --- Task 10: render_contribution from the service -------------------------

def test_render_contribution_builds_rows_and_reconciles():
    cog = _cog()
    captured = {}

    async def fake_reconcile(page_id, desired_rows, data_col_count, numeric_cols=()):
        captured["page_id"] = page_id
        captured["rows"] = desired_rows
        captured["cols"] = data_col_count

    cog.reconcile_table = fake_reconcile

    async def fake_ensure(page_id):
        return True

    cog._ensure_contribution_table = fake_ensure

    meta = PuzzleData()
    meta.id = 1
    meta.name = "Meta"
    meta.google_page_id = 900
    feeder = PuzzleData()
    feeder.id = 2
    feeder.name = "Alpha"
    feeder.solution = "WORD"
    feeder.tag_edges = {5: "feeds"}

    asyncio.run(cog.render_contribution(meta, tag_group_id=5,
                                        members=[meta, feeder], evidence=[]))
    assert captured["page_id"] == 900
    assert captured["cols"] == 4
    # the meta's own row is not a contribution of itself
    assert captured["rows"] == [["feeds:2", "Feeder", "Alpha", "WORD"]]


# --- Bold static labels (live-test fix #4) ----------------------------------

def test_label_cells_emit_bold_format():
    """generate_puzzle_tab must bold the captured label cells (the layout flags
    them bold; update_cell only writes the value). One repeatCell per bold label,
    column shifted by the target offset."""
    from bot.cogs import sheet_layout

    cog = _cog()
    cells, _ = sheet_layout.puzzle_tab_layout(is_target=True)
    bold_cells = [c for c in cells if c.get("bold")]
    assert bold_cells, "captured layout should flag labels bold"

    reqs = cog._label_bold_requests(page_id=900, cells=cells, offset=1)
    assert len(reqs) == len(bold_cells)
    for r in reqs:
        rc = r["repeatCell"]
        assert rc["range"]["sheetId"] == 900
        assert rc["cell"]["userEnteredFormat"]["textFormat"]["bold"] is True
        assert rc["fields"] == "userEnteredFormat.textFormat.bold"

    # A1 label with offset 1 lands in column index 1 (col B); feeder (offset 0) col A.
    cols_target = {r["repeatCell"]["range"]["startColumnIndex"] for r in reqs}
    assert 1 in cols_target
    feeder_reqs = cog._label_bold_requests(page_id=900, cells=cells, offset=0)
    assert 0 in {r["repeatCell"]["range"]["startColumnIndex"] for r in feeder_reqs}


# --- Unassigned tab far-left position (live-test fix #5) ---------------------

def test_ensure_unassigned_table_pins_tab_to_far_left():
    """The Unassigned Feeders tab is created with create_blank_tab (no index), so
    Sheets appends it last. ensure_unassigned_table must move it to index 0."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_page_name_by_id = lambda page_id, spreadsheet_id=None: "Unassigned Feeders"

    async def fake_values_update(spreadsheet_id, a1, values):
        pass

    cog._values_update = fake_values_update

    sheet = MagicMock()

    async def fake_execute(request, on_retry=None):
        return {"replies": [{"addTable": {"table": {"tableId": "UT"}}}]}

    with patch.object(gsheet_mod, "get_sheet", return_value=sheet), \
         patch.object(gsheet_mod, "execute_with_retry", side_effect=fake_execute):
        asyncio.run(cog.ensure_unassigned_table(page_id=700))

    bodies = [kw["body"] for _a, kw in sheet.batchUpdate.call_args_list if "body" in kw]
    moves = [r["updateSheetProperties"] for b in bodies for r in b["requests"]
             if "updateSheetProperties" in r]
    assert moves, "expected an updateSheetProperties to move the tab"
    assert moves[0]["properties"]["sheetId"] == 700
    assert moves[0]["properties"]["index"] == 0


# --- Resilience: transient Sheets 500 must not abort meta creation (#1) ------

def test_generate_puzzle_tab_survives_contribution_table_failure():
    """A transient Sheets error creating the contribution Table must NOT abort meta
    creation — generate_puzzle_tab returns the page id; the Table is deferred."""
    cog = _cog()
    _capture_batch(cog)   # header batch succeeds
    cog.create_blank_tab = lambda spreadsheet_id, name: 904
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_unique_tab_name = lambda n: n

    async def boom(page_id, header_row=0):
        raise RuntimeError("Internal error encountered.")

    cog._add_contribution_table = boom

    p = PuzzleData()
    p.id = 1
    p.name = "Meta"
    p.url = "http://m"
    p.is_target = True

    page_id = asyncio.run(cog.generate_puzzle_tab(p, is_target=True))
    assert page_id == 904   # creation completed despite the Table failure


def test_render_contribution_creates_missing_table_then_reconciles():
    """If the tab has no contribution Table (creation transiently failed earlier),
    render_contribution creates it before writing data."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.sheet_list = lambda spreadsheet_id=None: {
        "sheets": [{"properties": {"sheetId": 900, "title": "Meta"}}]}   # no "tables"

    created = []

    async def fake_add(page_id, header_row=0):
        created.append((page_id, header_row))

    reconciled = []

    async def fake_reconcile(page_id, desired_rows, data_col_count, numeric_cols=()):
        reconciled.append(page_id)

    cog._add_contribution_table = fake_add
    cog.reconcile_table = fake_reconcile

    meta = PuzzleData()
    meta.id = 1
    meta.name = "Meta"
    meta.google_page_id = 900

    asyncio.run(cog.render_contribution(meta, tag_group_id=5, members=[meta], evidence=[]))
    assert created and created[0][0] == 900   # table created lazily
    assert reconciled == [900]                 # then the data write proceeds


def test_render_contribution_skips_write_when_table_uncreatable():
    """If the Table still can't be created (sustained Sheets outage), skip the data
    write so contribution rows never clobber the header at row 0."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.sheet_list = lambda spreadsheet_id=None: {
        "sheets": [{"properties": {"sheetId": 900, "title": "Meta"}}]}

    async def boom(page_id, header_row=0):
        raise RuntimeError("Internal error encountered.")

    reconciled = []

    async def fake_reconcile(page_id, desired_rows, data_col_count, numeric_cols=()):
        reconciled.append(page_id)

    cog._add_contribution_table = boom
    cog.reconcile_table = fake_reconcile

    meta = PuzzleData()
    meta.id = 1
    meta.name = "Meta"
    meta.google_page_id = 900

    asyncio.run(cog.render_contribution(meta, tag_group_id=5, members=[meta], evidence=[]))
    assert reconciled == []   # no data written when the table couldn't be made

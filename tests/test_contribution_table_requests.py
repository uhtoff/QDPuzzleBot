"""Request-shape tests for the contribution table + shared reconciler (Milestone D).

Tasks 6-11 of docs/superpowers/plans/2026-06-25-milestone-d-sheet-generation.md.
No live Google: patch the cog's batch/low-level seams and assert the batchUpdate
request bodies. Mirrors tests/test_aggregate_to_tab.py (asyncio.run +
GoogleSheets.__new__). Helpers are file-local. Authored test-first (Opus);
implementation lands separately.
"""
import asyncio

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

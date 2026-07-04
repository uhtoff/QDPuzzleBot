"""Request-shape tests for the contribution table + shared reconciler (Milestone D).

Tasks 6-11 of docs/superpowers/plans/2026-06-25-milestone-d-sheet-generation.md.
No live Google: patch the cog's batch/low-level seams and assert the batchUpdate
request bodies. Mirrors tests/test_aggregate_to_tab.py (asyncio.run +
GoogleSheets.__new__). Helpers are file-local. Authored test-first (Opus);
implementation lands separately.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.cogs.puzzles_gsheet as gsheet_mod
from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.cogs import sheet_layout
from bot.store.puzzle_data import PuzzleData
from bot.utils import config


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

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{"properties": {"sheetId": 900, "title": "Meta", "index": 0}}]}
    cog.sheet_list = _fake_sheet_list

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

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Meta"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

    table_id = asyncio.run(cog._add_contribution_table(page_id=900))

    assert table_id == "TID"
    add = next(r["addTable"]["table"] for body in calls
               for r in body["requests"] if "addTable" in r)
    # Named with a page-unique, char-safe literal (Google rejects Table names with
    # '-'/apostrophes, and "X - meta" tabs would break addTable). The Table is
    # scan-located, never looked up by name.
    assert add["name"] == "Contributions 900"
    assert add["range"]["sheetId"] == 900
    # 4 columns A-D
    assert add["range"]["startColumnIndex"] == 0
    assert add["range"]["endColumnIndex"] == 4
    # The header NAMES are written into the header-row cells (A-D) BEFORE the
    # addTable in the same batch, so the Table anchors to exactly these 4 columns.
    # (An empty header row + columnProperties let Sheets pad the table with phantom
    # "Column N" columns and shift our names right — live-test bug.)
    add_body = next(b for b in calls if any("addTable" in r for r in b["requests"]))
    kinds = [next(iter(r)) for r in add_body["requests"]]
    assert kinds.index("updateCells") < kinds.index("addTable")
    hdr = next(r["updateCells"] for r in add_body["requests"] if "updateCells" in r)
    names = [next(iter(v["userEnteredValue"].values())) for v in hdr["rows"][0]["values"]]
    assert names == ["key", "Source", "Type", "Content"]
    assert hdr["range"]["startColumnIndex"] == 0 and hdr["range"]["endColumnIndex"] == 4
    # A hidden-column request is issued (key column hidden)
    hide = [r for body in calls for r in body["requests"]
            if "updateDimensionProperties" in r]
    assert hide and hide[0]["updateDimensionProperties"]["properties"]["hiddenByUser"] is True


# --- Task 7: reconcile_table (append / update / prune by column-A key) ------

def test_reconcile_table_appends_updates_prunes():
    cog = _cog()
    # current sheet has feeds:2 (row 1, will update) and informs:9 (row 2, will prune)
    sent = _capture_lowlevel(cog, ["feeds:2", "informs:9"])
    desired = [["feeds:2", "Feeder", "Alpha", "WORD"],   # update
               ["none:3", "Group", "Beta", ""]]          # append
    asyncio.run(cog.reconcile_table(page_id=900, desired_rows=desired,
                                    data_col_count=4))
    flat = [r for body in sent for r in body["requests"]]
    kinds = {k for r in flat for k in r}
    assert "updateCells" in kinds
    # Rows are never deleted — deleteDimension would shift any user-added columns
    # to the right of the table on every other row too.
    assert "deleteDimension" not in kinds
    updates = [r["updateCells"] for r in flat if "updateCells" in r]
    # informs:9 (row 2) is blanked in place, not removed.
    prune = next(u for u in updates if u["range"]["startRowIndex"] == 2)
    assert prune["rows"][0]["values"] == [{}, {}, {}, {}]
    # feeds:2 (row 1) is rewritten in place, keeping its row index.
    update = next(u for u in updates if u["range"]["startRowIndex"] == 1)
    assert update["rows"][0]["values"][0] == {"userEnteredValue": {"stringValue": "feeds:2"}}
    # none:3 is appended strictly after the highest row currently in use (row 2),
    # never reusing the blanked informs:9 row.
    append = next(u for u in updates if u["range"]["startRowIndex"] == 3)
    assert append["rows"][0]["values"][0] == {"userEnteredValue": {"stringValue": "none:3"}}


def test_reconcile_table_numeric_length_and_resizes_table():
    """Number-typed Length column is written numeric (blank when unsolved), and the
    native Table is resized to cover header + data so rows belong to it."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{
            "properties": {"sheetId": 700, "title": "Unassigned Feeders", "index": 0},
            "tables": [{"tableId": "UT", "range": {
                "sheetId": 700, "startRowIndex": 0, "startColumnIndex": 0,
                "endRowIndex": 1, "endColumnIndex": 5}}],
        }]}
    cog.sheet_list = _fake_sheet_list

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
    cog.create_blank_tab = lambda spreadsheet_id, name, index=None: 901
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_unique_tab_name(n):
        return n
    cog.get_unique_tab_name = _fake_get_unique_tab_name

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
    cog.create_blank_tab = lambda spreadsheet_id, name, index=None: 902
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_unique_tab_name(n):
        return n
    cog.get_unique_tab_name = _fake_get_unique_tab_name

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Meta"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

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
    cog.create_blank_tab = lambda spreadsheet_id, name, index=None: 903
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_unique_tab_name(n):
        return n
    cog.get_unique_tab_name = _fake_get_unique_tab_name

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

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{
            "properties": {"sheetId": 905, "title": "Meta"},
            "tables": [{"tableId": "T", "range": {"startRowIndex": 3, "startColumnIndex": 0}}]}]}
    cog.sheet_list = _fake_sheet_list

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

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Alpha"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

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


def _promote_alpha():
    """Run promote on a target tab; return the captured batchUpdate bodies."""
    cog = _cog()
    calls = _capture_batch(cog)
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def fake_values_get(spreadsheet_id, a1_range):
        return {"values": [["saved"]]}

    cog._values_get = fake_values_get

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Alpha"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

    p = PuzzleData()
    p.id = 1
    p.name = "Alpha"
    p.url = "http://a"
    p.google_page_id = 905
    asyncio.run(cog.promote_tab_to_target(p))
    return cog, calls


def _cell_value(r):
    # update_cell writes a single cell (values is a dict); the contribution header
    # write uses a list of cells — skip those (return None) so single-cell scans work.
    values = r["updateCells"]["rows"][0]["values"]
    if isinstance(values, list):
        return None
    return next(iter(values["userEnteredValue"].values()))


def test_promote_puts_table_at_a4():
    # End state must match a generated target: Table top-left A4 (col 0, below
    # the 3 frozen header rows), NOT row 0 inside the header.
    _cog_, calls = _promote_alpha()
    add = next(r["addTable"]["table"] for body in calls
              for r in body["requests"] if "addTable" in r)
    assert add["range"]["startRowIndex"] == sheet_layout.FROZEN_ROWS  # row 4
    assert add["range"]["startColumnIndex"] == 0                       # col A


def test_promote_regenerates_header_at_offset_1():
    # Header is regenerated at offset 1 (the generated-target geometry), NOT left
    # at the CONTRIBUTION_WIDTH offset where the column insert pushed it.
    cog, calls = _promote_alpha()
    name_col = cog.get_column(config.puzzle_cell_name) + cog.TARGET_HEADER_SHIFT
    hyperlinks = [r for body in calls for r in body["requests"]
                  if "updateCells" in r and "rows" in r["updateCells"]
                  and isinstance(_cell_value(r), str)
                  and "HYPERLINK" in _cell_value(r)]
    assert hyperlinks, "promote must regenerate the name hyperlink"
    assert hyperlinks[0]["updateCells"]["range"]["startColumnIndex"] == name_col
    # Title merge (base col 1) lands at offset 1 -> col 2, not col 1+4.
    merges = [r["mergeCells"]["range"] for body in calls
              for r in body["requests"] if "mergeCells" in r]
    assert any(m["startColumnIndex"] == 1 + cog.TARGET_HEADER_SHIFT for m in merges)


def test_promote_unmerges_header_before_remerging():
    # The insert pushes the old header merges right; they must be unmerged before
    # the canonical offset-1 merges are stamped, or Sheets rejects the overlap.
    _cog_, calls = _promote_alpha()
    seq = [k for body in calls for r in body["requests"] for k in r]
    assert "unmergeCells" in seq, "promote must unmerge the pushed header"
    assert seq.index("unmergeCells") < seq.index("mergeCells")


def test_header_shift_constant_for_target():
    # Both generated and promoted targets now share one geometry, so _header_shift
    # returns TARGET_HEADER_SHIFT for a target's own tab regardless of where the
    # Table sits (old code keyed off the Table's row).
    cog = _cog()

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [
            {"properties": {"sheetId": 905}, "tables": [{"range": {"startRowIndex": 0}}]}]}
    cog.sheet_list = _fake_sheet_list
    p = PuzzleData()
    p.id = 1
    p.is_target = True
    p.google_page_id = 905
    assert cog._header_shift(p, 905) == cog.TARGET_HEADER_SHIFT
    p.is_target = False
    assert cog._header_shift(p, 905) == 0


def test_header_shift_applies_to_minimeta():
    # A minimeta's own tab is promoted to the shifted target geometry but it is
    # not a tag_group target (is_target stays False), so the shift must key off
    # is_minimeta too — otherwise solution/progress writes land one column left.
    cog = _cog()

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [
            {"properties": {"sheetId": 905}, "tables": [{"range": {"startRowIndex": 0}}]}]}
    cog.sheet_list = _fake_sheet_list
    p = PuzzleData()
    p.id = 1
    p.is_target = False
    p.is_minimeta = True
    p.google_page_id = 905
    assert cog._header_shift(p, 905) == cog.TARGET_HEADER_SHIFT
    # A plain feeder (neither target nor minimeta) keeps column A.
    p.is_minimeta = False
    assert cog._header_shift(p, 905) == 0


def _demote_alpha():
    """Run demote on a target tab; return (cog, captured batchUpdate bodies)."""
    cog = _cog()
    calls = _capture_batch(cog)
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Alpha"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

    async def fake_values_get(spreadsheet_id, a1_range):
        return {"values": [["saved"]]}

    cog._values_get = fake_values_get

    async def fake_update_info(puzzle, update_tab_name=False, name=None, page_id=None):
        pass

    cog.update_puzzle_info = fake_update_info

    p = PuzzleData()
    p.id = 1
    p.name = "Alpha"
    p.url = "http://a"
    p.google_page_id = 905
    p.is_target = False  # convert_to_puzzle clears the flag before demote
    asyncio.run(cog.demote_tab_to_puzzle(p))
    return cog, calls


def test_demote_removes_contribution_columns():
    # Inverse of promote: drop the CONTRIBUTION_WIDTH left columns (and the Table
    # they hold); the solver grid slides back into place.
    cog, calls = _demote_alpha()

    deletes = [r["deleteDimension"] for body in calls for r in body["requests"]
               if "deleteDimension" in r]
    assert deletes, "demote must delete the contribution columns"
    rng = deletes[0]["range"]
    assert rng["sheetId"] == 905
    assert rng["dimension"] == "COLUMNS"
    assert rng["startIndex"] == 0
    assert rng["endIndex"] == cog.CONTRIBUTION_WIDTH


def test_demote_regenerates_plain_header_at_offset_0():
    # The column delete removes the offset-1 target header, so demote must
    # regenerate the plain puzzle header at offset 0 (labels back in column A),
    # unmerging the stale merges first.
    _cog_, calls = _demote_alpha()
    titles = [r for body in calls for r in body["requests"]
              if "updateCells" in r and "rows" in r["updateCells"]
              and _cell_value(r) == "Puzzle Title:"]
    assert titles, "demote must regenerate the static header labels"
    assert titles[0]["updateCells"]["range"]["startColumnIndex"] == 0  # offset 0 -> A
    seq = [k for body in calls for r in body["requests"] for k in r]
    assert seq.index("unmergeCells") < seq.index("mergeCells")


def test_demote_does_not_rename_tab():
    # demote regenerates the full header (incl. the name hyperlink) itself, so it
    # must NOT also call update_puzzle_info — its update_tab_name=True renames the
    # tab to its own name, which get_unique_tab_name then bumps to "Alpha (2)".
    cog = _cog()
    _capture_batch(cog)
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Alpha"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

    async def fake_values_get(spreadsheet_id, a1_range):
        return {"values": [["saved"]]}

    cog._values_get = fake_values_get
    info = []

    async def fake_info(*a, **k):
        info.append((a, k))

    cog.update_puzzle_info = fake_info
    p = PuzzleData()
    p.id = 1
    p.name = "Alpha"
    p.url = "http://a"
    p.google_page_id = 905
    p.is_target = False
    asyncio.run(cog.demote_tab_to_puzzle(p))
    assert info == []


def test_promote_to_group_does_not_rename_tab():
    # promote_tab_to_target already regenerates the header (incl. name), so the
    # group wrapper must not call update_puzzle_info (which renamed the tab).
    cog = _cog()
    promoted = []

    async def fake_promote(p):
        promoted.append(p)

    cog.promote_tab_to_target = fake_promote
    info = []

    async def fake_info(*a, **k):
        info.append((a, k))

    cog.update_puzzle_info = fake_info
    p = PuzzleData()
    p.id = 1
    p.name = "Alpha"
    p.google_page_id = 905
    asyncio.run(cog.promote_tab_to_group(p))
    assert promoted == [p]
    assert info == []


# --- Task 10: render_contribution from the service -------------------------

def test_render_contribution_builds_rows_and_reconciles():
    cog = _cog()
    captured = {}

    async def fake_reconcile(page_id, desired_rows, data_col_count,
                             numeric_cols=(), grey_below=False):
        captured["page_id"] = page_id
        captured["rows"] = desired_rows
        captured["cols"] = data_col_count
        captured["grey_below"] = grey_below

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
    feeder.tag_edges = {5: {"feeds"}}

    asyncio.run(cog.render_contribution(meta, tag_group_id=5,
                                        members=[meta, feeder], evidence=[]))
    assert captured["page_id"] == 900
    assert captured["cols"] == 4
    # the meta's own row is not a contribution of itself; columns key/source/type/content
    assert captured["rows"] == [["feeds:2", "Alpha", "Feeder", "WORD"]]
    # the contribution table greys its columns below the data (social contract)
    assert captured["grey_below"] is True


def test_render_contribution_includes_self_feeding_target():
    # A meta that feeds itself (self-edge 'feeds') is a contributor to its own
    # rollup and must appear on its own table.
    cog = _cog()
    captured = {}

    async def fake_reconcile(page_id, desired_rows, data_col_count,
                             numeric_cols=(), grey_below=False):
        captured["rows"] = desired_rows

    cog.reconcile_table = fake_reconcile

    async def fake_ensure(page_id):
        return True

    cog._ensure_contribution_table = fake_ensure

    meta = PuzzleData()
    meta.id = 1
    meta.name = "Meta"
    meta.solution = "LOOP"
    meta.google_page_id = 900
    meta.tag_edges = {5: {"feeds"}}  # feeds itself

    asyncio.run(cog.render_contribution(meta, tag_group_id=5,
                                        members=[meta], evidence=[]))
    assert captured["rows"] == [["feeds:1", "Meta", "Feeder", "LOOP"]]


def _reconcile_with_table(grey_below):
    """Drive reconcile_table over a sheet that HAS a native table at A4 (rowCount
    50), one desired row; return the captured batchUpdate bodies."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{
            "properties": {"sheetId": 900, "title": "Meta",
                           "gridProperties": {"rowCount": 50}},
            "tables": [{"tableId": "T", "range": {
                "startRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 4}}]}]}
    cog.sheet_list = _fake_sheet_list

    async def fake_values_get(spreadsheet_id, a1_range):
        return {"values": [["key"]]}  # header only, no existing data rows

    cog._values_get = fake_values_get
    sent = []

    async def fake_batch(body, archive=False):
        sent.append(body)

    cog.batch_update = fake_batch
    asyncio.run(cog.reconcile_table(
        page_id=900, desired_rows=[["feeds:2", "Feeder", "Alpha", "WORD"]],
        data_col_count=4, grey_below=grey_below))
    return sent


_SOCIAL_GREY = {"red": 0.95, "green": 0.95, "blue": 0.95}


def _bg_ranges(sent, color):
    return [r["repeatCell"]["range"] for body in sent for r in body["requests"]
            if "repeatCell" in r
            and r["repeatCell"]["cell"].get("userEnteredFormat", {})
                .get("backgroundColor") == color]


def _clear_ranges(sent):
    """repeatCells that CLEAR the background (no fill): backgroundColor named in
    the field mask but left unset in the cell."""
    return [r["repeatCell"]["range"] for body in sent for r in body["requests"]
            if "repeatCell" in r
            and "backgroundColor" in r["repeatCell"].get("fields", "")
            and "backgroundColor" not in r["repeatCell"]["cell"].get("userEnteredFormat", {})]


def test_reconcile_greys_below_table_when_requested():
    sent = _reconcile_with_table(grey_below=True)
    greys = _bg_ranges(sent, _SOCIAL_GREY)
    assert greys, "grey_below must grey the region below the table"
    rng = greys[0]
    # below header (row 3) + 1 header + 1 data row = row 5, down to rowCount 50, cols A-D
    assert rng["startRowIndex"] == 3 + 1 + 1
    assert rng["endRowIndex"] == 50
    assert rng["startColumnIndex"] == 0 and rng["endColumnIndex"] == 4


def test_reconcile_clears_grey_under_data_rows():
    # Rows that now hold data must have their fill cleared (no fill) so leftover
    # grey from a previous, shorter render doesn't show under newly-added entries.
    sent = _reconcile_with_table(grey_below=True)
    clears = _clear_ranges(sent)
    assert clears, "data rows must have their fill cleared (no fill)"
    rng = clears[0]
    assert rng["startRowIndex"] == 3 + 1        # first data row (header_row+1)
    assert rng["endRowIndex"] == 3 + 1 + 1      # end_row (one data row)
    assert rng["startColumnIndex"] == 0 and rng["endColumnIndex"] == 4


def test_reconcile_no_grey_by_default():
    # The shared reconciler (e.g. the Unassigned tab) must NOT grey below unless asked.
    sent = _reconcile_with_table(grey_below=False)
    assert _bg_ranges(sent, _SOCIAL_GREY) == []
    assert _clear_ranges(sent) == []


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


def test_dynamic_name_and_solution_cells_emit_bold_format():
    """The bot writes the puzzle name + solution as dynamic values with update_cell
    (userEnteredValue only), so their cells must be bolded at tab creation. One
    bold repeatCell per cell, shifted by the target offset."""
    from bot.utils import config

    cog = _cog()
    reqs = cog._dynamic_bold_requests(page_id=901, offset=1)
    assert len(reqs) == 2
    cols = set()
    for r in reqs:
        rc = r["repeatCell"]
        assert rc["range"]["sheetId"] == 901
        assert rc["cell"]["userEnteredFormat"]["textFormat"]["bold"] is True
        assert rc["fields"] == "userEnteredFormat.textFormat.bold"
        cols.add(rc["range"]["startColumnIndex"])
    # name (B1 -> col 1) + offset 1 = col 2; solution shifted likewise.
    assert cog.get_column(config.puzzle_cell_name) + 1 in cols
    assert cog.get_column(config.puzzle_cell_solution) + 1 in cols


def test_header_border_request_outlines_frozen_rows():
    """The frozen header rows get a thin border on every cell edge, spanning the
    A..Q header band shifted right by the target offset."""
    from bot.cogs import sheet_layout

    cog = _cog()
    r = cog._header_border_request(page_id=902, offset=1)["updateBorders"]
    assert r["range"]["sheetId"] == 902
    assert r["range"]["startRowIndex"] == 0
    assert r["range"]["endRowIndex"] == sheet_layout.FROZEN_ROWS
    assert r["range"]["startColumnIndex"] == 1                       # offset
    assert r["range"]["endColumnIndex"] == cog.HEADER_LAST_COL + 1   # band + offset
    for edge in ("top", "bottom", "left", "right", "innerHorizontal", "innerVertical"):
        assert r[edge]["style"] == "SOLID"


def test_generate_puzzle_tab_emits_bold_values_and_border():
    """End-to-end: a generated tab's batch carries the dynamic-value bold requests
    and the header border (alongside the existing label bold + tab styling)."""
    cog = _cog()
    calls = _capture_batch(cog)
    cog.create_blank_tab = lambda spreadsheet_id, name, index=None: 904
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_unique_tab_name(n):
        return n
    cog.get_unique_tab_name = _fake_get_unique_tab_name

    p = PuzzleData()
    p.id = 7
    p.name = "Feeder"
    p.url = "http://f"
    p.is_target = False

    asyncio.run(cog.generate_puzzle_tab(p, is_target=False))
    reqs = [r for body in calls for r in body["requests"]]

    bolds = [r["repeatCell"] for r in reqs if "repeatCell" in r
             and r["repeatCell"]["cell"]["userEnteredFormat"].get("textFormat", {}).get("bold")]
    assert bolds, "tab should carry bold textFormat requests"
    assert any("updateBorders" in r for r in reqs), "tab should carry a header border"


def test_create_blank_tab_inserts_at_index():
    """create_blank_tab plumbs the insert index into addSheet so new puzzle tabs
    land at the requested position (regression: tabs were appended at the end)."""
    cog = _cog()
    captured = {}

    class _Sheet:
        def batchUpdate(self, spreadsheetId, body):
            captured["body"] = body
            return "REQ"

    async def fake_exec(req, on_retry=None):
        return {"replies": [{"addSheet": {"properties": {"sheetId": 77}}}]}

    with patch.object(gsheet_mod, "get_sheet", lambda: _Sheet()), \
         patch.object(gsheet_mod, "execute_with_retry", fake_exec):
        pid = asyncio.run(cog.create_blank_tab("LIVE", "Alpha", index=7))
    assert pid == 77
    props = captured["body"]["requests"][0]["addSheet"]["properties"]
    assert props["index"] == 7
    assert props["title"] == "Alpha"


def test_create_blank_tab_appends_when_no_index():
    """Omitting the index appends at the end (no `index` key — Sheets default)."""
    cog = _cog()
    captured = {}

    class _Sheet:
        def batchUpdate(self, spreadsheetId, body):
            captured["body"] = body
            return "REQ"

    async def fake_exec(req, on_retry=None):
        return {"replies": [{"addSheet": {"properties": {"sheetId": 88}}}]}

    with patch.object(gsheet_mod, "get_sheet", lambda: _Sheet()), \
         patch.object(gsheet_mod, "execute_with_retry", fake_exec):
        asyncio.run(cog.create_blank_tab("LIVE", "Beta"))
    props = captured["body"]["requests"][0]["addSheet"]["properties"]
    assert "index" not in props


def test_generate_puzzle_tab_passes_index_to_create_blank_tab():
    """New puzzle tabs are inserted at INITIAL_OFFSET (left, after the reserved
    sheets); the index must reach create_blank_tab, not be dropped."""
    cog = _cog()
    _capture_batch(cog)
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_unique_tab_name(n):
        return n
    cog.get_unique_tab_name = _fake_get_unique_tab_name
    seen = {}

    def fake_blank(spreadsheet_id, name, index=None):
        seen["index"] = index
        return 906

    cog.create_blank_tab = fake_blank

    p = PuzzleData()
    p.id = 5
    p.name = "Alpha"
    p.url = "http://x"
    p.is_target = False

    asyncio.run(cog.generate_puzzle_tab(p, is_target=False, index=cog.INITIAL_OFFSET))
    assert seen["index"] == cog.INITIAL_OFFSET


# --- Unassigned tab far-left position (live-test fix #5) ---------------------

def test_ensure_unassigned_table_pins_tab_to_far_left():
    """The Unassigned Feeders tab is created with create_blank_tab (no index), so
    Sheets appends it last. ensure_unassigned_table must move it to index 0."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_page_name_by_id(page_id, spreadsheet_id=None):
        return "Unassigned Feeders"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

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


# --- Evidence tab: native Table + position after Unassigned Feeders ----------

def _run_ensure_evidence(page_id=800):
    """Drive ensure_evidence_table over a mocked Sheets API; return
    (header_updates, clears, batch_bodies)."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_page_name_by_id(pid, spreadsheet_id=None):
        return "Evidence"
    cog.get_page_name_by_id = _fake_get_page_name_by_id

    header_updates = []
    clears = []

    async def fake_values_update(spreadsheet_id, a1, values):
        header_updates.append((a1, values))

    async def fake_values_clear(spreadsheet_id, a1_range):
        clears.append(a1_range)

    cog._values_update = fake_values_update
    cog._values_clear = fake_values_clear

    sheet = MagicMock()

    async def fake_execute(request, on_retry=None):
        return {"replies": [{"addTable": {"table": {"tableId": "ET"}}}]}

    with patch.object(gsheet_mod, "get_sheet", return_value=sheet), \
         patch.object(gsheet_mod, "execute_with_retry", side_effect=fake_execute):
        table_id = asyncio.run(cog.ensure_evidence_table(page_id=page_id))

    bodies = [kw["body"] for _a, kw in sheet.batchUpdate.call_args_list if "body" in kw]
    return table_id, header_updates, clears, bodies


def test_ensure_evidence_table_pins_tab_after_unassigned():
    """The Evidence tab is created with create_blank_tab (no index), so Sheets
    appends it last. ensure_evidence_table must move it to index 1 — immediately
    after Unassigned Feeders (index 0)."""
    _tid, _hdr, _clr, bodies = _run_ensure_evidence(page_id=800)
    moves = [r["updateSheetProperties"] for b in bodies for r in b["requests"]
             if "updateSheetProperties" in r]
    assert moves, "expected an updateSheetProperties to move the tab"
    assert moves[0]["properties"]["sheetId"] == 800
    assert moves[0]["properties"]["index"] == 1


def test_ensure_evidence_table_adds_table_and_hides_id_col():
    """A native Table named 'Evidence' is created spanning the 5 bot-owned columns
    (id, Source Puzzle, Note, Target Puzzle, Logged At), the header is written to
    A1, and the bookkeeping id column is hidden."""
    table_id, header_updates, _clr, bodies = _run_ensure_evidence(page_id=800)

    assert table_id == "ET"

    add = next(r["addTable"]["table"] for b in bodies for r in b["requests"]
               if "addTable" in r)
    assert add["name"] == "Evidence"
    assert add["range"]["sheetId"] == 800
    assert add["range"]["startColumnIndex"] == 0
    assert add["range"]["endColumnIndex"] == 5   # 5 columns A-E

    # Header names written to A1 (plain, so data is visible even without the Table).
    assert header_updates, "expected the header row to be written"
    a1, values = header_updates[0]
    assert a1.endswith("!A1")
    assert values == [["id", "Source Puzzle", "Note", "Target Puzzle", "Logged At"]]

    # The bookkeeping id column (col 0) is hidden.
    hide = [r["updateDimensionProperties"] for b in bodies for r in b["requests"]
            if "updateDimensionProperties" in r]
    assert hide and hide[0]["properties"]["hiddenByUser"] is True
    assert hide[0]["range"]["startIndex"] == 0 and hide[0]["range"]["endIndex"] == 1


def test_ensure_evidence_table_clears_old_flat_render():
    """One-time migration: the old flat aggregate_to_tab render (header at A5 +
    rows) is cleared before the native Table is created at A1, so stale rows from
    the previous renderer don't linger below the Table."""
    _tid, _hdr, clears, _bodies = _run_ensure_evidence(page_id=800)
    assert clears, "expected the old evidence render to be cleared"
    # Whole-tab clear (range is the bare quoted sheet name).
    assert any("Evidence" in rng for rng in clears)


# --- Resilience: transient Sheets 500 must not abort meta creation (#1) ------

def test_generate_puzzle_tab_survives_contribution_table_failure():
    """A transient Sheets error creating the contribution Table must NOT abort meta
    creation — generate_puzzle_tab returns the page id; the Table is deferred."""
    cog = _cog()
    _capture_batch(cog)   # header batch succeeds
    cog.create_blank_tab = lambda spreadsheet_id, name, index=None: 904
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_get_unique_tab_name(n):
        return n
    cog.get_unique_tab_name = _fake_get_unique_tab_name

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

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{"properties": {"sheetId": 900, "title": "Meta"}}]}   # no "tables"
    cog.sheet_list = _fake_sheet_list
    # A1 isn't the plain-puzzle "Puzzle Title:" label, so this is the transient
    # (already-promoted) case: go straight to _add_contribution_table.
    cog.get_page_name_by_id = AsyncMock(return_value="Meta")
    cog._values_get = AsyncMock(return_value={"values": [[""]]})

    created = []

    async def fake_add(page_id, header_row=0):
        created.append((page_id, header_row))

    reconciled = []

    async def fake_reconcile(page_id, desired_rows, data_col_count, numeric_cols=(),
                             grey_below=False):
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


def test_render_contribution_promotes_never_promoted_legacy_tab():
    """A tab imported from a legacy hunt (e.g. Puzzle Boat 10) that never ran
    through promote_tab_to_target/promote_tab_to_group still carries the
    plain-puzzle header (the "Puzzle Title:" label at A1) instead of target
    layout. render_contribution must detect that via A1 and promote the tab
    (inserting the contribution columns) rather than colliding a direct
    addTable with the real content already sitting in A-D."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{"properties": {"sheetId": 900, "title": "Meta"}}]}   # no "tables"
    cog.sheet_list = _fake_sheet_list
    cog.get_page_name_by_id = AsyncMock(return_value="Meta")
    cog._values_get = AsyncMock(
        return_value={"values": [[sheet_layout.PUZZLE_TAB_CELLS[0]["value"]]]})

    promoted = []

    async def fake_promote(puzzle):
        promoted.append(puzzle.id)

    def fail_direct_add(page_id, header_row=0):
        raise AssertionError("must not addTable directly over a never-promoted tab")

    reconciled = []

    async def fake_reconcile(page_id, desired_rows, data_col_count, numeric_cols=(),
                             grey_below=False):
        reconciled.append(page_id)

    cog.promote_tab_to_target = fake_promote
    cog._add_contribution_table = fail_direct_add
    cog.reconcile_table = fake_reconcile

    meta = PuzzleData()
    meta.id = 1
    meta.name = "Meta"
    meta.google_page_id = 900

    asyncio.run(cog.render_contribution(meta, tag_group_id=5, members=[meta], evidence=[]))
    assert promoted == [1]
    assert reconciled == [900]


def test_render_contribution_skips_write_when_table_uncreatable():
    """If the Table still can't be created (sustained Sheets outage), the failure
    propagates (refresh_target/!rebuild badge + report it) rather than being
    swallowed, and no data write happens so contribution rows never clobber the
    header at row 0."""
    cog = _cog()
    cog.get_spreadsheet_id = lambda: "LIVE"

    async def _fake_sheet_list(spreadsheet_id=None):
        return {"sheets": [{"properties": {"sheetId": 900, "title": "Meta"}}]}
    cog.sheet_list = _fake_sheet_list
    cog.get_page_name_by_id = AsyncMock(return_value="Meta")
    cog._values_get = AsyncMock(return_value={"values": [[""]]})

    async def boom(page_id, header_row=0):
        raise RuntimeError("Internal error encountered.")

    reconciled = []

    async def fake_reconcile(page_id, desired_rows, data_col_count, numeric_cols=(),
                             grey_below=False):
        reconciled.append(page_id)

    cog._add_contribution_table = boom
    cog.reconcile_table = fake_reconcile

    meta = PuzzleData()
    meta.id = 1
    meta.name = "Meta"
    meta.google_page_id = 900

    with pytest.raises(RuntimeError):
        asyncio.run(cog.render_contribution(meta, tag_group_id=5, members=[meta], evidence=[]))
    assert reconciled == []   # no data written when the table couldn't be made

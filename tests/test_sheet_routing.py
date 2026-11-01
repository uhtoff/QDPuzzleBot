"""Every cog sheet operation on a puzzle's or extra's tab goes through the one
location rule (bot.core.sheet_location.spreadsheet_for), via cog.spreadsheet_of.

Before this, ~14 cog methods read the hunt's live spreadsheet implicitly and
batch_update picked live/solved from an `archive=` flag. That cannot address a
puzzle that lives in its own spreadsheet (files mode), and it silently missed a
solved minimeta's tab in a tab hunt (its roll-up render read the LIVE sheet,
found no such tab and returned).

Hunt-level tabs (Unassigned, Evidence, hunt sheets) keep the hunt's live
spreadsheet: `spreadsheet_id=None` means live.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.puzzles_gsheet import GoogleSheets
from bot.core.sheet_location import Link, SheetLocation
from bot.core.sheet_tables import contribution_table_name
from bot.store.puzzle_data import AdditionalSheetData
from tests.conftest import make_puzzle


def _cog():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog.get_spreadsheet_id = lambda: "LIVE"
    cog.get_solved_sheet_id = lambda: "SOLVED"
    return cog


def _record_batches(cog):
    sent = []

    async def fake(body, spreadsheet_id=None):
        sent.append(spreadsheet_id)
        return {"replies": [{"addTable": {"table": {"tableId": "TID"}}}]}
    cog.batch_update = fake
    return sent


def _puzzle(**kw):
    p = make_puzzle(name="Alpha")
    p.url = "http://x"
    p.google_page_id = 900
    p.solution = "ANSWER"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# --- the rule itself, as the cog reads it ------------------------------------

def test_spreadsheet_of_follows_the_location_rule():
    cog = _cog()
    assert cog.spreadsheet_of(_puzzle(google_sheet_id="F")) == "F"
    assert cog.spreadsheet_of(_puzzle(google_sheet_id="F", on_solved_sheet=True)) == "F"
    assert cog.spreadsheet_of(_puzzle(on_solved_sheet=True)) == "SOLVED"
    assert cog.spreadsheet_of(_puzzle()) == "LIVE"
    cog.get_solved_sheet_id = lambda: ""
    assert cog.spreadsheet_of(_puzzle(on_solved_sheet=True)) == "LIVE"


def test_batch_update_none_is_live_and_an_id_is_used_as_given():
    cog = _cog()
    api = MagicMock()
    with patch("bot.cogs.puzzles_gsheet.get_sheet", return_value=api), \
         patch("bot.cogs.puzzles_gsheet.execute_with_retry", new=AsyncMock(return_value={})):
        asyncio.run(cog.batch_update({"requests": []}))
        asyncio.run(cog.batch_update({"requests": []}, spreadsheet_id="F"))
    ids = [c.kwargs["spreadsheetId"] for c in api.batchUpdate.call_args_list]
    assert ids == ["LIVE", "F"]


# --- a puzzle with its own spreadsheet ---------------------------------------

def test_update_solution_writes_to_the_puzzles_own_spreadsheet():
    cog = _cog()
    sent = _record_batches(cog)
    asyncio.run(cog.update_solution(_puzzle(google_sheet_id="F")))
    assert sent == ["F"]


def test_update_puzzle_info_writes_and_reads_names_on_the_puzzles_own_spreadsheet():
    cog = _cog()
    sent = _record_batches(cog)
    listed = []

    async def fake_sheet_list(spreadsheet_id=None):
        listed.append(spreadsheet_id)
        return {"sheets": [{"properties": {"sheetId": 900, "title": "Old", "index": 0}}]}
    cog.sheet_list = fake_sheet_list

    asyncio.run(cog.update_puzzle_info(_puzzle(google_sheet_id="F"), update_tab_name=True))
    assert listed == ["F"]   # the unique-name check reads the puzzle's own spreadsheet
    assert sent == ["F"]


def test_delete_puzzle_spreadsheet_deletes_from_the_puzzles_own_spreadsheet():
    # A mini carries its minimeta's file id: its TAB is deleted, routed to
    # that file. (A puzzle with its own file and no parent loses the whole
    # file instead -- tests/test_files_mode_create.py, ruling R1.)
    cog = _cog()
    sent = _record_batches(cog)
    asyncio.run(cog.delete_puzzle_spreadsheet(
        _puzzle(google_sheet_id="F", parent_puzzle_id=3)))
    assert sent == ["F"]


def _table_sheet(page_id, title="Alpha"):
    return {"properties": {"sheetId": page_id, "title": title, "index": 0},
            "tables": [{"name": contribution_table_name(page_id), "tableId": "T",
                        "range": {"startRowIndex": 3, "startColumnIndex": 0,
                                  "endColumnIndex": 4}}]}


def _sheets_only_on(cog, spreadsheet_id, page_id):
    """sheet_list answers the tab (with its Table) only for `spreadsheet_id`;
    any other spreadsheet has no such tab. Records every read."""
    reads = []

    async def fake_sheet_list(sid=None):
        reads.append(("list", sid))
        return {"sheets": [_table_sheet(page_id)] if sid == spreadsheet_id else
                [{"properties": {"sheetId": 1, "title": "Other", "index": 0}}]}

    async def fake_values_get(sid, a1_range):
        reads.append(("values", sid))
        return {"values": [["key"]]}
    cog.sheet_list = fake_sheet_list
    cog._values_get = fake_values_get
    return reads


def test_render_contribution_reconciles_on_the_puzzles_own_spreadsheet():
    cog = _cog()
    sent = _record_batches(cog)
    reads = _sheets_only_on(cog, "F", 900)
    target = _puzzle(google_sheet_id="F", is_target=True)
    feeder = make_puzzle(id=2, name="Feeder")
    feeder.add_edge(7, "feeds")

    asyncio.run(cog.render_contribution(target, 7, [feeder], []))
    assert reads and all(sid == "F" for _kind, sid in reads), reads
    assert sent == ["F"]   # the reconcile's one write batch


def test_an_extra_with_its_own_spreadsheet_is_deleted_there():
    """The service's extra-sheet delete routes by the EXTRA's location."""
    from bot.core.puzzle_service import PuzzleService
    cog = _cog()
    sent = _record_batches(cog)
    bot = MagicMock()
    bot.get_cog.return_value = cog
    svc = PuzzleService(bot)
    hunt = MagicMock(google_sheet_id="LIVE", solved_sheet_id="SOLVED")
    # set_* write the ContextVars; the getters are stubbed, so keep them inert.
    cog.set_spreadsheet_id = lambda _sid: None
    cog.set_solved_sheet_id = lambda _sid: None
    puzzle = _puzzle(solved=True)
    extra = AdditionalSheetData(id=5, google_page_id="77", google_sheet_id="X")
    puzzle.additional_sheets = [extra]

    with patch("bot.core.puzzle_service.SheetsJsonDb"):
        asyncio.run(svc._delete_additional_sheet(hunt, puzzle, extra))
    assert sent == ["X"]


# --- a tab-hunt puzzle on the solved spreadsheet -----------------------------

def test_a_tabs_puzzle_on_the_solved_sheet_routes_to_solved():
    cog = _cog()
    sent = _record_batches(cog)
    p = _puzzle(on_solved_sheet=True)
    asyncio.run(cog.update_solution(p))
    asyncio.run(cog.update_puzzle_info(p))
    asyncio.run(cog.delete_puzzle_spreadsheet(p))
    assert sent == ["SOLVED", "SOLVED", "SOLVED"]


def test_a_solved_tab_hunt_minimetas_table_renders_on_the_solved_spreadsheet():
    """Accepted consequence of the rule (complication 9): the minimeta's tab
    moved to the solved spreadsheet on solve, so its roll-up renders there.
    Before, the render read the LIVE sheet, found no tab and silently returned."""
    cog = _cog()
    sent = _record_batches(cog)
    reads = _sheets_only_on(cog, "SOLVED", 900)
    minimeta = _puzzle(on_solved_sheet=True, solved=True)
    mini = make_puzzle(id=3, name="Mini", solution="x")

    asyncio.run(cog.render_minimeta_contribution(minimeta, [mini]))
    assert reads and all(sid == "SOLVED" for _kind, sid in reads), reads
    assert sent == ["SOLVED"]


# --- header links go where each location says --------------------------------

def test_write_header_links_sends_one_batch_to_each_of_three_spreadsheets():
    cog = _cog()
    api = MagicMock()
    with patch("bot.cogs.puzzles_gsheet.get_sheet", return_value=api), \
         patch("bot.cogs.puzzles_gsheet.execute_with_retry", new=AsyncMock(return_value={})):
        failed = asyncio.run(cog.write_header_links([
            (SheetLocation("LIVE", 901), 0, [Link("M", "U")]),
            (SheetLocation("SOLVED", 902), 0, []),
            (SheetLocation("F", 903), 1, [Link("N", "V")]),
            (SheetLocation("", 904), 0, []),       # no spreadsheet: skipped
            (SheetLocation("F", ""), 0, []),       # no page: skipped
        ]))
    ids = sorted(c.kwargs["spreadsheetId"] for c in api.batchUpdate.call_args_list)
    assert ids == ["F", "LIVE", "SOLVED"]
    assert failed == []

"""Stop paying for metadata and round-trips nobody uses (M21).

Pure optimisation -- no state is corrupted by any of this. Two fixes here:

  * sheet_list asked for the FULL spreadsheet payload on all 15 of its call
    sites. An audit of every caller found they read only ["sheets"],
    ["properties"] (sheetId / title / index / hidden) and .get("tables"), so a
    fields mask is free.
  * move_puzzle_to_solved_sheet asked Google for the tab count once PER
    additional sheet to compute the same end index every time. Nothing moves
    between those calls -- the requests are collected and sent in one
    batch_update afterwards -- so every fetch after the first returned an
    identical answer.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle
from bot.store.puzzle_data import AdditionalSheetData


def make_sheets_cog():
    from bot.cogs.puzzles_gsheet import GoogleSheets
    with patch("bot.cogs.puzzles_gsheet.get_sheet") as mock_get_sheet:
        mock_get_sheet.return_value = MagicMock()
        cog = GoogleSheets.__new__(GoogleSheets)
        cog.sheets_service = mock_get_sheet.return_value
    return cog


@pytest.mark.asyncio
async def test_sheet_list_asks_only_for_the_fields_callers_read():
    """A fields mask on the one call every Sheets read goes through."""
    cog = make_sheets_cog()
    api = MagicMock()
    api.get.return_value = "REQ"

    with (
        patch("bot.cogs.puzzles_gsheet.get_sheet", return_value=api),
        patch("bot.cogs.puzzles_gsheet.execute_with_retry",
              new=AsyncMock(return_value={"sheets": []})),
        patch.object(cog, "get_spreadsheet_id", return_value="SS"),
    ):
        await cog.sheet_list()

    fields = api.get.call_args.kwargs.get("fields")
    assert fields, "sheet_list must send a fields mask"
    # Everything any caller reads, and nothing else.
    assert "properties" in fields
    assert "tables" in fields


@pytest.mark.asyncio
async def test_move_sheet_to_end_skips_the_fetch_when_given_an_index():
    cog = make_sheets_cog()
    listing = AsyncMock(return_value={"sheets": [{}, {}, {}]})
    with patch.object(cog, "sheet_list", new=listing):
        req = await cog.move_sheet_to_end("page-1", end_index=7)

    listing.assert_not_awaited()
    assert req["updateSheetProperties"]["properties"]["index"] == 7


@pytest.mark.asyncio
async def test_move_sheet_to_end_still_fetches_when_not_given_one():
    """The default path must be unchanged for every other caller."""
    cog = make_sheets_cog()
    listing = AsyncMock(return_value={"sheets": [{}, {}, {}]})
    with patch.object(cog, "sheet_list", new=listing):
        req = await cog.move_sheet_to_end("page-1")

    listing.assert_awaited_once()
    assert req["updateSheetProperties"]["properties"]["index"] == 3


@pytest.mark.asyncio
async def test_the_tab_count_is_fetched_once_however_many_extras_there_are():
    """Two extras, so a per-item fetch (3) and a shared one (1) disagree.

    get_page_name_by_id is stubbed because it calls sheet_list too -- this
    counts only the fetches the end-index computation is responsible for.
    """
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = True
    puzzle.additional_sheets = [
        AdditionalSheetData(google_page_id="extra-1"),
        AdditionalSheetData(google_page_id="extra-2"),
    ]
    listing = AsyncMock(return_value={"sheets": [{}, {}, {}, {}]})

    with (
        patch.object(cog, "sheet_list", new=listing),
        patch.object(cog, "batch_update", new=AsyncMock()),
        patch.object(cog, "_solution_bg_request", return_value={}),
        patch.object(cog, "_header_shift", return_value=0),
        patch.object(cog, "change_tab_colour", return_value={}),
        patch.object(cog, "update_cell", return_value={}),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Extra")),
        patch.object(cog, "move_puzzle_sheet", new=AsyncMock(return_value="moved")),
        patch.object(cog, "get_solved_sheet_id", return_value="ARCHIVE"),
        patch("bot.cogs.puzzles_gsheet.config") as cfg,
    ):
        cfg.puzzle_cell_solution = "B1"
        cfg.puzzle_cell_progress = "B3"
        await cog.move_puzzle_to_solved_sheet(puzzle)

    assert listing.await_count == 1, (
        f"end index re-fetched per sheet: {listing.await_count} calls for 2 extras")


@pytest.mark.asyncio
@pytest.mark.parametrize("is_target", [False, True])
async def test_compact_tab_create_budget(is_target):
    """A compact create is 1 create_blank_tab + 2 batch_updates (the header batch,
    then update_puzzle_info's title rewrite) -- and a compact target's Table rides
    in the header batch, so _add_contribution_table is never called."""
    cog = make_sheets_cog()
    puzzle = make_puzzle(name="Alpha", is_target=is_target)
    puzzle.header_layout = "compact"
    puzzle.url = "https://h/alpha"
    cog.spreadsheet_of = lambda p: "SS"
    cog.get_unique_tab_name = AsyncMock(return_value="Alpha")
    cog.create_blank_tab = AsyncMock(return_value=900)
    cog.batch_update = AsyncMock(return_value={"replies": []})
    cog._add_contribution_table = AsyncMock()

    await cog.create_puzzle_spreadsheet(puzzle)

    assert cog.create_blank_tab.await_count == 1
    assert cog.batch_update.await_count == 2
    cog._add_contribution_table.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_promote_and_demote_budgets():
    """Each is 1 sheet_list read + 1 batch_update, with no tab-name or A1:B1 probe."""
    cog = make_sheets_cog()
    puzzle = make_puzzle(name="Alpha", is_target=True)
    puzzle.header_layout = "compact"
    puzzle.google_page_id = 900
    cog.spreadsheet_of = lambda p: "SS"
    cog.batch_update = AsyncMock(return_value={"replies": []})
    cog.get_page_name_by_id = AsyncMock(return_value="Alpha")
    cog._values_get = AsyncMock(return_value={})
    page = {"properties": {"sheetId": 900, "title": "Alpha"}}
    cog.sheet_list = AsyncMock(return_value={"sheets": [page]})

    await cog.promote_tab_to_target(puzzle)
    assert cog.sheet_list.await_count == 1
    assert cog.batch_update.await_count == 1

    cog.sheet_list = AsyncMock(return_value={"sheets": [
        {**page, "tables": [{"tableId": "T", "name": "Contributions 900"}]}]})
    cog.batch_update.reset_mock()
    await cog.demote_tab_to_puzzle(puzzle)
    assert cog.sheet_list.await_count == 1
    assert cog.batch_update.await_count == 1
    cog.get_page_name_by_id.assert_not_awaited()
    cog._values_get.assert_not_awaited()

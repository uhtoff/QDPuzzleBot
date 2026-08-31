"""An additional sheet records where it actually lives (H3, M15).

`move_puzzle_to_solved_sheet` archives EVERY extra sheet whenever the hunt has a
solved spreadsheet, but only a NON-target puzzle gets `on_solved_sheet = True`.
So for a solved meta/capstone the extras sat on the archive while the puzzle row
said "not archived", and nothing recorded where an individual extra was.

`restore_puzzle_from_solved_sheet` then keyed every extra off the PUZZLE's flag.
For a target that is False, so the extras were not moved back -- yet their
update_cell / move_sheet_to_index / revert_tab_colour requests were still batched
against the MAIN spreadsheet using page ids that exist only in the archive.
Sheets answers 400, which is not retryable, and unsolve has no try/except around
the call.

Migration 045 gives `additional_sheets` the location bit `puzzles` has carried
since 024. These tests pin that each sheet is moved, flagged and linked by its
OWN location rather than its puzzle's.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.store.puzzle_data import AdditionalSheetData
from tests.conftest import make_puzzle


def make_sheets_cog():
    from bot.cogs.puzzles_gsheet import GoogleSheets
    with patch("bot.cogs.puzzles_gsheet.get_sheet") as mock_get_sheet:
        mock_get_sheet.return_value = MagicMock()
        cog = GoogleSheets.__new__(GoogleSheets)
        cog.sheets_service = mock_get_sheet.return_value
    return cog


def _cfg(mock_cfg):
    mock_cfg.puzzle_cell_solution = "B1"
    mock_cfg.puzzle_cell_progress = "B3"


# ---------------------------------------------------------------------------
# H3 -- archiving flags each extra
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_targets_extras_are_flagged_when_they_are_archived():
    """A target's own tab stays on the live sheet, but its extras still move.
    Before 045 nothing recorded that, which is the whole of H3."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = True          # target: its OWN tab is not archived
    puzzle.additional_sheets = [AdditionalSheetData(google_page_id="extra-1")]

    with (
        patch.object(cog, "batch_update", new=AsyncMock()),
        patch.object(cog, "move_sheet_to_end", new=AsyncMock(return_value={})),
        patch.object(cog, "_solution_bg_request", return_value={}),
        patch.object(cog, "_header_shift", return_value=0),
        patch.object(cog, "change_tab_colour", return_value={}),
        patch.object(cog, "update_cell", return_value={}),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Extra")),
        patch.object(cog, "move_puzzle_sheet", new=AsyncMock(return_value="archived-1")),
        patch.object(cog, "get_solved_sheet_id", return_value="archive-id"),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        _cfg(mock_cfg)
        await cog.move_puzzle_to_solved_sheet(puzzle)

    assert puzzle.additional_sheets[0].on_solved_sheet is True
    assert puzzle.additional_sheets[0].google_page_id == "archived-1"
    # The target's own tab is deliberately NOT archived.
    assert puzzle.on_solved_sheet is not True


@pytest.mark.asyncio
async def test_an_extra_with_no_page_id_is_never_archived():
    """The `len(google_page_id) > 0` guard the in-place loop has, which the
    archive loop was missing."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = True
    puzzle.additional_sheets = [AdditionalSheetData(google_page_id="")]

    move = AsyncMock(return_value="should-not-happen")
    with (
        patch.object(cog, "batch_update", new=AsyncMock()),
        patch.object(cog, "move_sheet_to_end", new=AsyncMock(return_value={})),
        patch.object(cog, "_solution_bg_request", return_value={}),
        patch.object(cog, "_header_shift", return_value=0),
        patch.object(cog, "change_tab_colour", return_value={}),
        patch.object(cog, "update_cell", return_value={}),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Extra")),
        patch.object(cog, "move_puzzle_sheet", new=move),
        patch.object(cog, "get_solved_sheet_id", return_value="archive-id"),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        _cfg(mock_cfg)
        await cog.move_puzzle_to_solved_sheet(puzzle)

    move.assert_not_awaited()
    assert puzzle.additional_sheets[0].on_solved_sheet is False


# ---------------------------------------------------------------------------
# H3 -- restore consults each extra's own flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_moves_back_only_the_extras_that_were_archived():
    """The load-bearing one. TWO extras in different places, on a TARGET whose
    own on_solved_sheet is False -- so the old puzzle-level gate moved back
    neither, then addressed both against the main spreadsheet.

    Two sheets, not one: with a single extra, "move them all" and "move the
    archived one" agree, and the test proves nothing.
    """
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = True
    puzzle.on_solved_sheet = False       # a target is never archived itself
    puzzle.solved = False
    puzzle.additional_sheets = [
        AdditionalSheetData(google_page_id="archived-extra", on_solved_sheet=True),
        AdditionalSheetData(google_page_id="live-extra", on_solved_sheet=False),
    ]

    move = AsyncMock(return_value="restored-extra")
    with (
        patch.object(cog, "batch_update", new=AsyncMock()),
        patch.object(cog, "_solution_bg_request", return_value={}),
        patch.object(cog, "_header_shift", return_value=0),
        patch.object(cog, "revert_tab_colour", return_value={}),
        patch.object(cog, "move_sheet_to_index", return_value={}),
        patch.object(cog, "update_cell", return_value={}),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Extra")),
        patch.object(cog, "move_puzzle_sheet", new=move),
        patch.object(cog, "get_spreadsheet_id", return_value="sheet-id"),
        patch.object(cog, "get_solved_sheet_id", return_value="archive-id"),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        _cfg(mock_cfg)
        await cog.restore_puzzle_from_solved_sheet(puzzle)

    # Exactly one move: the archived extra. Not zero (the old bug), not two.
    assert move.await_count == 1
    moved_page_ids = [c.args[1] for c in move.await_args_list]
    assert moved_page_ids == ["archived-extra"]

    assert puzzle.additional_sheets[0].google_page_id == "restored-extra"
    assert puzzle.additional_sheets[0].on_solved_sheet is False   # cleared on return
    assert puzzle.additional_sheets[1].google_page_id == "live-extra"  # untouched
    assert puzzle.additional_sheets[1].on_solved_sheet is False


# ---------------------------------------------------------------------------
# M15 -- links follow the sheet, not puzzle.solved
# ---------------------------------------------------------------------------

def _cog_for_links():
    from bot.cogs.puzzles import Puzzles
    return Puzzles.__new__(Puzzles)


def test_links_use_each_sheets_own_location():
    """Two extras in different places must produce two different spreadsheet
    ids. Keyed on the puzzle, both came out the same, which is M15."""
    from bot.store.hunt_data import HuntData
    cog = _cog_for_links()
    hunt = HuntData()
    hunt.google_sheet_id = "LIVE"
    hunt.solved_sheet_id = "ARCHIVE"
    puzzle = make_puzzle()
    puzzle.solved = True
    puzzle.additional_sheets = [
        AdditionalSheetData(google_page_id="1", puzzle_name="A", on_solved_sheet=True),
        AdditionalSheetData(google_page_id="2", puzzle_name="B", on_solved_sheet=False),
    ]

    links = cog.create_additional_sheet_list(None, puzzle, hunt)

    assert "ARCHIVE" in links.split("\n")[0]
    assert "LIVE" in links.split("\n")[1]


def test_a_solved_puzzle_on_a_hunt_with_no_solved_sheet_links_to_the_live_sheet():
    """M15's dead link: solved_sheet_id defaults to "", so keying on
    puzzle.solved built 'https://docs.google.com/spreadsheets/d//edit#gid=...'."""
    from bot.store.hunt_data import HuntData
    cog = _cog_for_links()
    hunt = HuntData()
    hunt.google_sheet_id = "LIVE"
    hunt.solved_sheet_id = ""          # hunt never had a solved sheet
    puzzle = make_puzzle()
    puzzle.solved = True
    puzzle.additional_sheets = [
        AdditionalSheetData(google_page_id="1", puzzle_name="A", on_solved_sheet=False)]

    links = cog.create_additional_sheet_list(None, puzzle, hunt)

    assert "LIVE" in links
    assert "/d//" not in links

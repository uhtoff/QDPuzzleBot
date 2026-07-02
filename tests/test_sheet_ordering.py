"""Tests for GoogleSheets.compute_insert_index and zone-aware insertion/restore."""

from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from tests.conftest import make_puzzle
from bot.store.puzzle_data import AdditionalSheetData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sheets_cog():
    """Return a GoogleSheets cog with mocked Sheets API."""
    # make_cog() creates the full Puzzles cog; we need the GoogleSheets cog.
    # Instantiate it directly, mocking the service build.
    from bot.cogs.puzzles_gsheet import GoogleSheets
    with patch("bot.cogs.puzzles_gsheet.get_sheet") as mock_get_sheet:
        mock_get_sheet.return_value = MagicMock()
        cog = GoogleSheets.__new__(GoogleSheets)
        cog.sheets_service = mock_get_sheet.return_value
    return cog


def _sheet_entry(sheet_id, index, hidden=False):
    """Build a minimal sheet entry as returned by sheet_list()."""
    return {
        "properties": {
            "sheetId": sheet_id,
            "index": index,
            "hidden": hidden,
            "title": f"Sheet {sheet_id}",
        }
    }


def _make_puzzle_with_sheet(page_id, is_target, solved):
    p = make_puzzle()
    p.google_page_id = page_id
    p.is_target = is_target
    p.solved = solved
    return p


# ---------------------------------------------------------------------------
# compute_insert_index: no existing puzzle tabs → INITIAL_OFFSET
# ---------------------------------------------------------------------------

async def test_compute_insert_index_no_puzzles_target(tmp_path):
    cog = make_sheets_cog()
    # Sheet list has only reserved slots (indices 0-6), no puzzle tabs
    reserved = [_sheet_entry(i, i) for i in range(7)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": reserved})):
        idx = await cog.compute_insert_index(is_target=True, puzzles=[])
    assert idx == cog.INITIAL_OFFSET


async def test_compute_insert_index_no_puzzles_regular(tmp_path):
    cog = make_sheets_cog()
    reserved = [_sheet_entry(i, i) for i in range(7)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": reserved})):
        idx = await cog.compute_insert_index(is_target=False, puzzles=[])
    assert idx == cog.INITIAL_OFFSET


# ---------------------------------------------------------------------------
# compute_insert_index: target goes after last unsolved target
# ---------------------------------------------------------------------------

async def test_compute_insert_index_target_after_existing_target():
    cog = make_sheets_cog()
    target_puzzle = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    # Target is at sheet index 7
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(101, 7)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=True, puzzles=[target_puzzle])
    assert idx == 8   # after the target at index 7


async def test_compute_insert_index_target_after_last_of_multiple_targets():
    cog = make_sheets_cog()
    t1 = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    t2 = _make_puzzle_with_sheet(page_id=102, is_target=True, solved=False)
    sheets = (
        [_sheet_entry(i, i) for i in range(7)]
        + [_sheet_entry(101, 7), _sheet_entry(102, 8)]
    )
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=True, puzzles=[t1, t2])
    assert idx == 9   # after index 8


# ---------------------------------------------------------------------------
# compute_insert_index: regular puzzle positioning
# ---------------------------------------------------------------------------

async def test_compute_insert_index_regular_after_existing_regular():
    cog = make_sheets_cog()
    r1 = _make_puzzle_with_sheet(page_id=201, is_target=False, solved=False)
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(201, 7)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=False, puzzles=[r1])
    assert idx == 8


async def test_compute_insert_index_regular_after_target_when_no_regulars():
    """With only a target existing, a new regular goes right after the target."""
    cog = make_sheets_cog()
    t1 = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(101, 7)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=False, puzzles=[t1])
    assert idx == 8


async def test_compute_insert_index_regular_after_last_regular_not_last_target():
    """Regular goes after the last regular, even if there's a target later
    (shouldn't happen in normal use but the algorithm must handle it)."""
    cog = make_sheets_cog()
    t1 = _make_puzzle_with_sheet(page_id=101, is_target=True, solved=False)
    r1 = _make_puzzle_with_sheet(page_id=201, is_target=False, solved=False)
    # target at 7, regular at 8
    sheets = (
        [_sheet_entry(i, i) for i in range(7)]
        + [_sheet_entry(101, 7), _sheet_entry(201, 8)]
    )
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=False, puzzles=[t1, r1])
    assert idx == 9  # after last regular (8), not last target (7)


# ---------------------------------------------------------------------------
# compute_insert_index: solved tabs are excluded from zone anchors
# ---------------------------------------------------------------------------

async def test_compute_insert_index_ignores_solved_puzzles():
    """A solved puzzle's tab (at the end) must not anchor the unsolved zone."""
    cog = make_sheets_cog()
    solved = _make_puzzle_with_sheet(page_id=301, is_target=False, solved=True)
    # Solved tab is at index 20 (end); no unsolved tabs exist
    sheets = [_sheet_entry(i, i) for i in range(7)] + [_sheet_entry(301, 20)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=False, puzzles=[solved])
    assert idx == cog.INITIAL_OFFSET  # falls back, not 21


# ---------------------------------------------------------------------------
# compute_insert_index: hidden sheets skipped
# ---------------------------------------------------------------------------

async def test_compute_insert_index_skips_hidden_sheets():
    cog = make_sheets_cog()
    r1 = _make_puzzle_with_sheet(page_id=201, is_target=False, solved=False)
    # r1's sheet is hidden (shouldn't happen in practice but ensure robustness)
    sheets = [_sheet_entry(201, 7, hidden=True)]
    with patch.object(cog, "sheet_list", new=AsyncMock(return_value={"sheets": sheets})):
        idx = await cog.compute_insert_index(is_target=False, puzzles=[r1])
    assert idx == cog.INITIAL_OFFSET  # hidden tab not counted


# ---------------------------------------------------------------------------
# create_puzzle_spreadsheet: uses passed index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_puzzle_spreadsheet_uses_passed_index():
    """When an index is passed, add_new_puzzle_sheet receives that index."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.is_target = False

    with (
        patch.object(cog, "add_new_puzzle_sheet", new_callable=AsyncMock) as mock_add,
        patch.object(cog, "update_puzzle_info", new_callable=AsyncMock),
    ):
        await cog.create_puzzle_spreadsheet(puzzle, index=12)
    mock_add.assert_awaited_once_with(puzzle, 12)


@pytest.mark.asyncio
async def test_create_puzzle_spreadsheet_defaults_to_initial_offset():
    """When no index is passed, falls back to INITIAL_OFFSET."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()

    with (
        patch.object(cog, "add_new_puzzle_sheet", new_callable=AsyncMock) as mock_add,
        patch.object(cog, "update_puzzle_info", new_callable=AsyncMock),
    ):
        await cog.create_puzzle_spreadsheet(puzzle)
    mock_add.assert_awaited_once_with(puzzle, cog.INITIAL_OFFSET)


# ---------------------------------------------------------------------------
# move_sheet_to_index: builds correct request
# ---------------------------------------------------------------------------

def test_move_sheet_to_index_request():
    cog = make_sheets_cog()
    req = cog.move_sheet_to_index(sheet_id=42, index=5)
    assert req["updateSheetProperties"]["properties"]["sheetId"] == 42
    assert req["updateSheetProperties"]["properties"]["index"] == 5


def test_move_sheet_to_start_delegates_to_initial_offset():
    cog = make_sheets_cog()
    req = cog.move_sheet_to_start(sheet_id=42)
    assert req["updateSheetProperties"]["properties"]["index"] == cog.INITIAL_OFFSET


# ---------------------------------------------------------------------------
# restore_puzzle_from_solved_sheet: uses restore_index for main + additional tabs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_puzzle_from_solved_sheet_uses_restore_index():
    """When restore_index is passed, the move request targets that index."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = False
    puzzle.on_solved_sheet = False
    puzzle.additional_sheets = []

    captured_requests = []

    async def fake_batch_update(body, archive=False):
        captured_requests.extend(body.get("requests", []))

    with (
        patch.object(cog, "batch_update", side_effect=fake_batch_update),
        patch.object(cog, "get_spreadsheet_id", return_value="sheet-id"),
        patch.object(cog, "get_solved_sheet_id", return_value=None),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        mock_cfg.puzzle_cell_solution = "B1"
        mock_cfg.puzzle_cell_progress = "B3"
        await cog.restore_puzzle_from_solved_sheet(puzzle, restore_index=9)

    move_reqs = [
        r for r in captured_requests
        if "updateSheetProperties" in r
        and "index" in r["updateSheetProperties"]["properties"]
    ]
    assert move_reqs, "Expected at least one index-setting updateSheetProperties request"
    for req in move_reqs:
        assert req["updateSheetProperties"]["properties"]["index"] == 9, (
            f"Expected index 9 but got {req['updateSheetProperties']['properties']['index']}"
        )


@pytest.mark.asyncio
async def test_restore_puzzle_from_solved_sheet_defaults_to_initial_offset():
    """When restore_index is omitted, falls back to INITIAL_OFFSET."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = False
    puzzle.on_solved_sheet = False
    puzzle.additional_sheets = []

    captured_requests = []

    async def fake_batch_update(body, archive=False):
        captured_requests.extend(body.get("requests", []))

    with (
        patch.object(cog, "batch_update", side_effect=fake_batch_update),
        patch.object(cog, "get_spreadsheet_id", return_value="sheet-id"),
        patch.object(cog, "get_solved_sheet_id", return_value=None),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        mock_cfg.puzzle_cell_solution = "B1"
        mock_cfg.puzzle_cell_progress = "B3"
        await cog.restore_puzzle_from_solved_sheet(puzzle)

    move_reqs = [
        r for r in captured_requests
        if "updateSheetProperties" in r
        and "index" in r["updateSheetProperties"]["properties"]
    ]
    for req in move_reqs:
        assert req["updateSheetProperties"]["properties"]["index"] == cog.INITIAL_OFFSET


@pytest.mark.asyncio
async def test_restore_puzzle_from_solved_sheet_moves_additional_sheets_back_when_archived():
    """Additional sheets must move back from the archive whenever the puzzle
    itself was archived — not gated on puzzle_data.solved, which unsolve()
    already cleared to False before calling restore (#2)."""
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 99
    puzzle.is_target = False
    puzzle.on_solved_sheet = True
    puzzle.solved = False  # unsolve() clears this before restore is called
    puzzle.additional_sheets = [AdditionalSheetData(google_page_id="archive-extra")]

    async def fake_batch_update(body, archive=False):
        pass

    with (
        patch.object(cog, "batch_update", side_effect=fake_batch_update),
        patch.object(cog, "move_puzzle_sheet", new=AsyncMock(return_value="live-extra")) as mock_move,
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Extra Sheet")),
        patch.object(cog, "get_spreadsheet_id", return_value="sheet-id"),
        patch.object(cog, "get_solved_sheet_id", return_value="archive-id"),
        patch("bot.cogs.puzzles_gsheet.config") as mock_cfg,
    ):
        mock_cfg.puzzle_cell_solution = "B1"
        mock_cfg.puzzle_cell_progress = "B3"
        await cog.restore_puzzle_from_solved_sheet(puzzle)

    # Once for the main page, once for the additional sheet.
    assert mock_move.await_count == 2
    assert puzzle.additional_sheets[0].google_page_id == "live-extra"

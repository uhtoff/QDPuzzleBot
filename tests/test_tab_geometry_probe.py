"""Probe a tab's geometry before shifting it (M18, C5).

Both bugs are the same shape: a mutation that assumes the tab is in the layout
the previous step was supposed to leave it in.

  M18 -- promote_tab_to_target inserts CONTRIBUTION_WIDTH columns, then stamps
         the header, then adds the Table. If the Table step fails the tab is
         promoted-but-tableless, and the next render calls promote AGAIN,
         inserting four MORE columns and walking the solver grid rightwards.
  C5  -- demote_tab_to_puzzle deletes those columns. A retry after a
         mid-operation failure deletes four more -- of real solver work.

The D7 live probe (2026-08-31) settled how to tell the states apart, and
corrected the review while it was at it:

  * a second deleteDimension of the same range SUCCEEDS SILENTLY. There is no
    error to catch, so C5 can only be prevented, never handled.
  * A1 ALONE IS NOT ENOUGH. A tab whose columns were inserted but whose header
    was never stamped returns the same empty A1 as a correctly promoted one --
    the response has no "values" key in both cases. The review said to reuse
    "the same check _ensure_contribution_table uses", but that check reads only
    A1. The probe must read A1:B1 and key on B1.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

LABEL = "Puzzle Title:"


def make_sheets_cog():
    from bot.cogs.puzzles_gsheet import GoogleSheets
    with patch("bot.cogs.puzzles_gsheet.get_sheet") as mock_get_sheet:
        mock_get_sheet.return_value = MagicMock()
        cog = GoogleSheets.__new__(GoogleSheets)
        cog.sheets_service = mock_get_sheet.return_value
    return cog


def _values_response(row):
    """Sheets omits "values" entirely for an empty range -- confirmed live."""
    return {"values": [row]} if row else {"majorDimension": "ROWS"}


def _dim_requests(batch_update_mock, kind):
    """Every insertDimension/deleteDimension request actually sent."""
    out = []
    for call in batch_update_mock.await_args_list:
        body = call.args[0] if call.args else call.kwargs.get("body", {})
        for req in body.get("requests", []):
            if kind in req:
                out.append(req[kind])
    return out


# ---------------------------------------------------------------------------
# The probe itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("row, expected", [
    ([LABEL, ""], "plain"),        # offset 0
    (["", LABEL], "target"),       # offset 1 -- already promoted
    ([], "unknown"),               # columns inserted, header never stamped
    (["something else"], "unknown"),
])
async def test_the_probe_reads_a1_and_b1(row, expected):
    """A1 alone cannot separate 'target' from 'unknown' -- both have an empty
    A1. Only B1 distinguishes them, which is the D7 finding."""
    cog = make_sheets_cog()
    with (
        patch.object(cog, "get_spreadsheet_id", return_value="SS"),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Tab")),
        patch.object(cog, "_values_get", new=AsyncMock(return_value=_values_response(row))) as vg,
    ):
        assert await cog._tab_layout("77") == expected

    # One read, and it must span A1:B1 -- an A1-only range cannot answer this.
    vg.assert_awaited_once()
    assert vg.await_args.args[1].endswith("!A1:B1")


# ---------------------------------------------------------------------------
# M18 -- promote is idempotent
# ---------------------------------------------------------------------------

def _promote_patches(cog, row):
    return (
        patch.object(cog, "get_spreadsheet_id", return_value="SS"),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Tab")),
        patch.object(cog, "_values_get", new=AsyncMock(return_value=_values_response(row))),
        patch.object(cog, "batch_update", new=AsyncMock()),
        patch.object(cog, "_add_contribution_table", new=AsyncMock()),
        patch.object(cog, "_puzzle_tab_header_requests", return_value=[]),
        patch.object(cog, "update_cell", return_value={}),
    )


@pytest.mark.asyncio
async def test_promote_on_an_already_promoted_tab_inserts_no_columns():
    """The M18 window: addTable failed, so the tab is promoted-but-tableless.
    The retry must add the Table WITHOUT shifting the grid again."""
    from tests.conftest import make_puzzle
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 77
    p = _promote_patches(cog, ["", LABEL])          # already target layout
    with p[0], p[1], p[2], p[3] as bu, p[4] as add, p[5], p[6]:
        await cog.promote_tab_to_target(puzzle)

    assert _dim_requests(bu, "insertDimension") == []
    add.assert_awaited_once()


@pytest.mark.asyncio
async def test_promote_on_a_plain_tab_inserts_exactly_once():
    from tests.conftest import make_puzzle
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 77
    p = _promote_patches(cog, [LABEL, ""])          # plain layout
    with p[0], p[1], p[2], p[3] as bu, p[4] as add, p[5], p[6]:
        await cog.promote_tab_to_target(puzzle)

    inserts = _dim_requests(bu, "insertDimension")
    assert len(inserts) == 1, inserts
    assert inserts[0]["range"]["endIndex"] == cog.CONTRIBUTION_WIDTH
    add.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_half_promoted_tab_is_not_shifted_again():
    """Columns in, header never stamped -- indistinguishable from promoted by A1.
    Inserting again is the exact damage M18 describes, so it must not happen."""
    from tests.conftest import make_puzzle
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 77
    p = _promote_patches(cog, [])                   # unknown
    with p[0], p[1], p[2], p[3] as bu, p[4], p[5], p[6]:
        await cog.promote_tab_to_target(puzzle)

    assert _dim_requests(bu, "insertDimension") == []


# ---------------------------------------------------------------------------
# C5 -- demote never deletes columns twice
# ---------------------------------------------------------------------------

def _demote_patches(cog, row):
    return (
        patch.object(cog, "get_spreadsheet_id", return_value="SS"),
        patch.object(cog, "get_page_name_by_id", new=AsyncMock(return_value="Tab")),
        patch.object(cog, "_values_get", new=AsyncMock(return_value=_values_response(row))),
        patch.object(cog, "batch_update", new=AsyncMock()),
        patch.object(cog, "_puzzle_tab_header_requests", return_value=[]),
        patch.object(cog, "update_cell", return_value={}),
    )


@pytest.mark.asyncio
async def test_demote_on_an_already_plain_tab_deletes_no_columns():
    """C5's data loss. The tab is already back at offset 0, so a retry that
    deletes CONTRIBUTION_WIDTH columns eats four columns of solver work -- and
    Sheets raises nothing, so only this guard prevents it."""
    from tests.conftest import make_puzzle
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 77
    p = _demote_patches(cog, [LABEL, ""])           # already plain
    with p[0], p[1], p[2], p[3] as bu, p[4], p[5]:
        await cog.demote_tab_to_puzzle(puzzle)

    assert _dim_requests(bu, "deleteDimension") == []


@pytest.mark.asyncio
async def test_demote_on_a_target_tab_deletes_exactly_once():
    from tests.conftest import make_puzzle
    cog = make_sheets_cog()
    puzzle = make_puzzle()
    puzzle.google_page_id = 77
    p = _demote_patches(cog, ["", LABEL])           # target layout
    with p[0], p[1], p[2], p[3] as bu, p[4], p[5]:
        await cog.demote_tab_to_puzzle(puzzle)

    deletes = _dim_requests(bu, "deleteDimension")
    assert len(deletes) == 1, deletes
    assert deletes[0]["range"]["endIndex"] == cog.CONTRIBUTION_WIDTH

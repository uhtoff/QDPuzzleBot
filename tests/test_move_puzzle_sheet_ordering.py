"""move_puzzle_sheet must never leave the DB pointing at a deleted tab (M19).

The sequence was copy -> DELETE the original -> rename the copy. The new page id
escapes only through the return value, so a failure in the rename window left the
original tab deleted, the copy sitting there as "Copy of X", and the caller's
`sheet.google_page_id = await move_puzzle_sheet(...)` never executed -- the DB
still pointing at a tab that no longer exists. Nothing heals that: _reconcile_tabs
repairs a puzzle's MAIN tab only and never its additional sheets.

That is not hypothetical. Migration 045's pre-flight turned up six additional_sheets
rows on puzzlebot_test whose page ids exist on neither the main nor the solved
spreadsheet -- dangling exactly this way.

The fix is ordering, not error handling: do the destructive step LAST, so every
failure window leaves the DB pointing at a tab that still exists. Copy, rename,
then delete.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_sheets_cog():
    from bot.cogs.puzzles_gsheet import GoogleSheets
    with patch("bot.cogs.puzzles_gsheet.get_sheet") as mock_get_sheet:
        mock_get_sheet.return_value = MagicMock()
        cog = GoogleSheets.__new__(GoogleSheets)
        cog.sheets_service = mock_get_sheet.return_value
    return cog


def _harness(cog, calls, fail_on=None):
    """Patch the cog so every API step records itself in `calls`.

    `fail_on` is "rename" or "delete" -- that step raises instead.
    """
    sheet_api = MagicMock()
    sheet_api.sheets.return_value.copyTo.return_value = {"kind": "copy"}
    sheet_api.batchUpdate.return_value = {"kind": "rename"}

    async def fake_execute(req, on_retry=None):
        kind = req["kind"]
        calls.append(kind)
        if kind == fail_on:
            raise RuntimeError(f"sheets 500 during {kind}")
        return {"sheetId": 777} if kind == "copy" else {}

    async def fake_delete(page, archive):
        calls.append("delete")
        if fail_on == "delete":
            raise RuntimeError("sheets 500 during delete")

    return (
        patch("bot.cogs.puzzles_gsheet.get_sheet", return_value=sheet_api),
        patch("bot.cogs.puzzles_gsheet.execute_with_retry", side_effect=fake_execute),
        patch.object(cog, "delete_sheet", side_effect=fake_delete),
        patch.object(cog, "get_unique_tab_name", new=AsyncMock(return_value="Alpha")),
        patch.object(cog, "get_spreadsheet_id", return_value="LIVE"),
        patch.object(cog, "get_solved_sheet_id", return_value="ARCHIVE"),
        patch.object(cog, "set_sheet_name", return_value={}),
    )


@pytest.mark.asyncio
async def test_the_destructive_delete_happens_last():
    """Pins the ordering invariant itself. Under the old order the list came
    back ['copy', 'delete', 'rename']."""
    cog = make_sheets_cog()
    calls = []
    p1, p2, p3, p4, p5, p6, p7 = _harness(cog, calls)
    with p1, p2, p3, p4, p5, p6, p7:
        new_id = await cog.move_puzzle_sheet(True, "old-page", "Alpha")

    assert calls == ["copy", "rename", "delete"], calls
    assert new_id == 777
    assert calls.index("delete") > calls.index("rename")


@pytest.mark.asyncio
async def test_a_rename_failure_leaves_the_original_tab_alive():
    """The M19 window. The original must NOT have been deleted, so the page id
    the DB still holds keeps pointing at a real tab."""
    cog = make_sheets_cog()
    calls = []
    p1, p2, p3, p4, p5, p6, p7 = _harness(cog, calls, fail_on="rename")
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(RuntimeError):
            await cog.move_puzzle_sheet(True, "old-page", "Alpha")

    assert "delete" not in calls, calls
    assert calls == ["copy", "rename"], calls


@pytest.mark.asyncio
async def test_a_delete_failure_also_leaves_a_tab_the_db_can_point_at():
    """The remaining window. The copy is made and named but the original
    survives; the caller does not reassign, so the DB keeps the original -- which
    still exists. A duplicate tab is recoverable by hand; a dangling id is not.
    """
    cog = make_sheets_cog()
    calls = []
    p1, p2, p3, p4, p5, p6, p7 = _harness(cog, calls, fail_on="delete")
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(RuntimeError):
            await cog.move_puzzle_sheet(True, "old-page", "Alpha")

    assert calls == ["copy", "rename", "delete"], calls

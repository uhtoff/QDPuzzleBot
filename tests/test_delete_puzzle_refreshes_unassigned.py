"""Deleting a puzzle must refresh the Unassigned Feeders sheet tab.

The board drops the puzzle at once (delete_puzzle pushes it, and the board reads
the DB), but the sheet tab is a native Table reconciled only when something
calls refresh_unassigned. Neither delete entry point did, so a deleted
unassigned feeder's row lingered until an unrelated mutation fired a refresh.

The refresh lives in the shared seam (PuzzleService.delete_puzzle) so both the
!delete_puzzle bang and the web DELETE route get it.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


async def _delete(svc, hunt, puzzle, children=None):
    children = children or {}
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "refresh_targets", new=AsyncMock(return_value=[])), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)), \
         patch.object(svc, "refresh_unassigned_safe", new=AsyncMock()) as refresh:
        pdb.get_children.side_effect = lambda pid: children.get(pid, [])
        await svc.delete_puzzle(None, hunt, puzzle)
        # Let the fire-and-forget task run while the mock is still installed.
        import asyncio
        await asyncio.sleep(0)
    return refresh


@pytest.mark.asyncio
async def test_deleting_a_puzzle_refreshes_the_unassigned_tab_for_its_hunt():
    svc = _svc()
    hunt = make_hunt()
    puzzle = make_puzzle(id=1, tags=[], channel_id=None)

    refresh = await _delete(svc, hunt, puzzle)

    refresh.assert_awaited_once_with(hunt)


@pytest.mark.asyncio
async def test_deleting_a_minimeta_parent_refreshes_once_not_per_mini():
    """Minis are never on the tab (parent_puzzle_id set), and the parent's own
    refresh runs after they are gone -- so TWO minis must still mean ONE refresh."""
    svc = _svc()
    hunt = make_hunt()
    parent = make_puzzle(id=1, tags=[], channel_id=None)
    mini_a = make_puzzle(id=2, tags=[], channel_id=None, parent_puzzle_id=1)
    mini_b = make_puzzle(id=3, tags=[], channel_id=None, parent_puzzle_id=1)

    refresh = await _delete(svc, hunt, parent, children={1: [mini_a, mini_b]})

    refresh.assert_awaited_once_with(hunt)

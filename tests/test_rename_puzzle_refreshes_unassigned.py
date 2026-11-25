"""Renaming a puzzle must refresh the Unassigned Feeders sheet tab.

The tab is a native Table reconciled only when something calls
refresh_unassigned, and rename_puzzle refreshed the metas the puzzle feeds but
not this tab -- so a renamed unassigned feeder kept its old name there until an
unrelated mutation fired a refresh.

The refresh lives in the shared seam (PuzzleService.rename_puzzle), which both
the !rename_puzzle bang and the web rename route call.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


async def _rename(svc, hunt, puzzle, name):
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "refresh_unassigned_safe", new=AsyncMock()) as refresh:
        await svc.rename_puzzle(None, hunt, puzzle, name)
        # Let the fire-and-forget task run while the mock is still installed.
        await asyncio.sleep(0)
    return refresh


@pytest.mark.asyncio
async def test_renaming_a_puzzle_refreshes_the_unassigned_tab_for_its_hunt():
    svc = _svc()
    hunt = make_hunt()
    puzzle = make_puzzle(id=1, name="Old", tags=[], channel_id=None)

    refresh = await _rename(svc, hunt, puzzle, "New")

    refresh.assert_awaited_once_with(hunt)


@pytest.mark.asyncio
async def test_renaming_a_mini_does_not_refresh_the_unassigned_tab():
    """Minis are never on the tab, so their rename has nothing to reconcile."""
    svc = _svc()
    hunt = make_hunt()
    mini = make_puzzle(id=2, name="Old", tags=[], channel_id=None, parent_puzzle_id=1)

    refresh = await _rename(svc, hunt, mini, "New")

    refresh.assert_not_awaited()

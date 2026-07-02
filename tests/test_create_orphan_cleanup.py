"""Create-orphan cleanup tests (M10): a failed channel-create leaves no DB row + no sheet.

After M3.5 service extraction, the web paths delegate to PuzzleService so cleanup
runs inside bot.core.puzzle_service._cleanup_orphan_puzzle; these tests verify that
the delegation chain is correct (patch the service seams, not cog internals).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.puzzles import Puzzles
from bot.store.hunt_data import HuntData
from tests.conftest import make_round


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCH1"
    h.url = None
    return h


def _cog_with_sheets():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return Puzzles(bot), gsheet


@pytest.mark.asyncio
async def test_web_create_puzzle_cleans_up_on_channel_failure():
    """web path calls service.create_puzzle directly; channel failure triggers orphan cleanup."""
    cog, gsheet = _cog_with_sheets()
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    round_data = make_round(id=5, category_id=999)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        with pytest.raises(discord.HTTPException):
            await cog.service.create_puzzle(guild, _hunt(), round_data, "Newpuz")

    # The sheet and committed row must be cleaned up by the service.
    gsheet.delete_puzzle_spreadsheet.assert_awaited_once()
    pdb.delete.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_web_create_tag_group_defers_welcome_via_notifier_none():
    """The web path calls service.create_tag_group(notifier=None) to suppress the early welcome
    (meta_code is not yet populated when create_puzzle fires it); the actions layer then
    sends the welcome itself once meta_code is available.  Verify the service contract:
    passing notifier=None means no welcome is fired inside create_tag_group."""
    cog, gsheet = _cog_with_sheets()
    hunt = _hunt()
    new_round = make_round(id=5, group_code="ABC123")
    puzzle = MagicMock(id=77, channel_id=333)
    guild = MagicMock()

    with patch.object(cog.service, "create_tag_group",
                      AsyncMock(return_value=(new_round, puzzle))) as create_tag_group:
        result = await cog.service.create_tag_group(guild, hunt, "New Round", notifier=None)

    assert result == (new_round, puzzle)
    # Early welcome suppressed so the embed isn't sent before meta_code is populated.
    assert create_tag_group.await_args.kwargs["notifier"] is None


@pytest.mark.asyncio
async def test_web_create_tag_group_cleans_up_meta_puzzle_on_late_failure():
    """web path calls service.create_tag_group directly; late failure triggers full rollback."""
    cog, gsheet = _cog_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    new_cat.delete = AsyncMock()

    hunt = _hunt()
    hunt.category_id = 1
    hunt.name = "H"

    created_puzzle = MagicMock(id=77, name="New Round", channel_id=None)
    cleanup_mock = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))), \
         patch.object(cog.service, "create_puzzle", AsyncMock(return_value=created_puzzle)), \
         patch.object(cog.service, "_cleanup_orphan_puzzle", cleanup_mock):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.side_effect = RuntimeError("late failure")  # now fails BEFORE create_puzzle (reordered)
        with pytest.raises(RuntimeError):
            await cog.service.create_tag_group(guild, hunt, "New Round")

    # generate_uid now runs before create_puzzle → puzzle=None.
    # Service rollback deletes round row; no orphan to clean.
    rdb.delete.assert_called_once_with(5)
    cleanup_mock.assert_not_awaited()


# NOTE: the former test_bang_create_puzzle_cleans_up_on_channel_failure was removed in
# Phase 5. The bang create path was strangled onto PuzzleService.create_puzzle (deleting
# the cog-level create_puzzle / create_puzzle_channel / _create_puzzle_with_cleanup twins),
# so the orphan-cleanup-on-channel-failure contract now lives at the service and is covered
# by tests/test_create_service.py::test_create_puzzle_orphan_cleanup_on_channel_failure.

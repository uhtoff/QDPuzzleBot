"""M3.5: PuzzleService.create_puzzle/create_round orchestration + cleanup.

These mirror the existing tests/test_create_orphan_cleanup.py wiring but target the
SERVICE, which calls the *module* function bot.core.discord_channels.get_or_create_channel
(not a cog attribute) and the relocated bot.core.puzzle_service.PuzzleJsonDb / TagGroupJsonDb.
Helpers are uniquely named and local to this file (parallel gate shards by file).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round, make_puzzle


def _svc_hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.category_id = 1
    h.name = "H"
    return h


def _service_with_sheets():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet


@pytest.mark.asyncio
async def test_create_puzzle_commits_row_creates_sheet_and_notifies():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    cat = MagicMock(id=999)
    guild.categories = [cat]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    gsheet.create_puzzle_spreadsheet.assert_awaited_once()
    notifier.welcome.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_puzzle_orphan_cleanup_on_channel_failure():
    # Relocated from test_create_orphan_cleanup.py — now targets the SERVICE.
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        with pytest.raises(discord.HTTPException):
            await svc.create_puzzle(guild, _svc_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    gsheet.delete_puzzle_spreadsheet.assert_awaited_once()
    pdb.delete.assert_called_once_with(1)
    notifier.welcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_round_creates_round_and_meta_puzzle():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    notifier = MagicMock()
    notifier.welcome = AsyncMock()

    # The service makes the meta puzzle via its own create_puzzle; stub that seam so
    # this test isolates the round/meta orchestration (mirrors web_create_round :820).
    meta_puzzle = make_puzzle(id=77, name="New Round", is_target=True, channel_id=222)
    guild.get_channel = MagicMock(return_value=MagicMock(id=222))

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))), \
         patch.object(svc, "create_puzzle", AsyncMock(return_value=meta_puzzle)):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "META01"
        result = await svc.create_tag_group(guild, _svc_hunt(), "New Round", notifier=notifier)

    new_round, puzzle = result
    assert puzzle is meta_puzzle
    assert new_round.target_puzzle_id == meta_puzzle.id
    assert new_round.group_code == "META01"


@pytest.mark.asyncio
async def test_create_round_rolls_back_on_failure():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    new_cat.delete = AsyncMock()
    notifier = MagicMock()
    notifier.welcome = AsyncMock()

    meta_puzzle = make_puzzle(id=77, name="New Round", is_target=True, channel_id=222)
    cleanup_mock = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))), \
         patch.object(svc, "create_puzzle", AsyncMock(return_value=meta_puzzle)), \
         patch.object(svc, "_cleanup_orphan_puzzle", cleanup_mock):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.side_effect = RuntimeError("late failure")  # fails AFTER create_puzzle
        with pytest.raises(RuntimeError):
            await svc.create_tag_group(guild, _svc_hunt(), "New Round", notifier=notifier)

    # Round row deleted, orphan meta puzzle cleaned, cloned category removed.
    rdb.delete.assert_called_once_with(5)
    cleanup_mock.assert_awaited_once()
    new_cat.delete.assert_awaited_once()

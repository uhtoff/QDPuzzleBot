"""Create-orphan cleanup tests (M10): a failed channel-create leaves no DB row + no sheet."""
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
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return Puzzles(bot), gsheet


@pytest.mark.asyncio
async def test_web_create_puzzle_cleans_up_on_channel_failure():
    cog, gsheet = _cog_with_sheets()
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    round_data = make_round(id=5, category_id=999)

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch.object(cog, "get_or_create_channel",
                      AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))):
        # Mirror the real store: commit assigns the auto-increment id, so by the
        # time cleanup runs the puzzle has a real (non-zero) id.
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        with pytest.raises(discord.HTTPException):
            await cog.web_create_puzzle(guild, _hunt(), round_data, "Newpuz")

    # The sheet that was created must be deleted, and the committed row removed.
    gsheet.delete_puzzle_spreadsheet.assert_awaited_once()
    pdb.delete.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_web_create_round_cleans_up_meta_puzzle_on_late_failure():
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
    hunt.parallel_hunt = False
    hunt.name = "H"

    created_puzzle = MagicMock(id=77, channel_id=None)

    cleanup_mock = AsyncMock()
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch("bot.cogs.puzzles.RoundJsonDb") as rdb, \
         patch.object(cog, "web_create_puzzle", AsyncMock(return_value=created_puzzle)), \
         patch.object(cog, "_cleanup_orphan_puzzle", cleanup_mock), \
         patch.object(cog, "get_or_create_channel", AsyncMock()):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.generate_uid.side_effect = RuntimeError("late failure")  # fails AFTER web_create_puzzle
        with pytest.raises(RuntimeError):
            await cog.web_create_round(guild, hunt, "New Round")

    cleanup_mock.assert_awaited_once_with(hunt, created_puzzle)


@pytest.mark.asyncio
async def test_bang_create_puzzle_cleans_up_on_channel_failure(monkeypatch):
    import bot.cogs.puzzles as puzzles_mod
    cog, gsheet = _cog_with_sheets()
    new_puzzle = MagicMock(id=88, tags=[])

    cog._cleanup_orphan_puzzle = AsyncMock()
    monkeypatch.setattr(cog, "create_puzzle", AsyncMock(return_value=new_puzzle))
    monkeypatch.setattr(cog, "create_puzzle_channel",
                        AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom")))

    hunt = _hunt()
    cat = MagicMock()
    chan = MagicMock(category=cat)
    ctx = MagicMock()
    ctx.channel = chan
    ctx.message.id = 7
    cog.environment[7] = {"channel_type": "Puzzle", "hunt": hunt, "hunt_round": None,
                          "puzzle": None, "guild": MagicMock(), "guild_data": None, "channel": chan}
    monkeypatch.setattr(cog, "get_tag_from_category", MagicMock(return_value=None))

    with pytest.raises(discord.HTTPException):
        await cog._create_puzzle_with_cleanup(ctx, "Newpuz")

    cog._cleanup_orphan_puzzle.assert_awaited_once()

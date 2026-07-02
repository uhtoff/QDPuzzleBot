"""Phase 5c: PuzzleService.delete_hunt — tear down a hunt (puzzles, rounds, sheets, DB row).

Asserts the stable teardown contract: spreadsheet FILE delete(s), the hunt DB delete, and
failed-channel isolation. Helpers are uniquely named and local to this file.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _delete_guild():
    guild = MagicMock()
    guild.categories = []
    return guild


def _delete_service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _delete_hunt_obj():
    h = HuntData()
    h.id = 7
    h.name = "H"
    h.category_id = 50
    h.channel_id = 60
    h.google_sheet_id = "SHEET_ID"
    h.archive_google_sheet_id = "ARCH_ID"
    return h


@pytest.mark.asyncio
async def test_delete_hunt_deletes_db_row_and_spreadsheet_files():
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    # The NEW whole-file delete method (added in Phase 3) — deletes the Drive file by id.
    gsheet.delete_hunt_spreadsheet = AsyncMock()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = []
        await svc.delete_hunt(guild, hunt)
    hdb.delete.assert_called_once_with(7)
    # Both the working and archive spreadsheet files are deleted (assert by file id).
    called_ids = [c.args[0] for c in gsheet.delete_hunt_spreadsheet.call_args_list]
    assert "SHEET_ID" in called_ids
    assert "ARCH_ID" in called_ids


@pytest.mark.asyncio
async def test_delete_hunt_returns_failed_channels():
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()
    one = MagicMock(); one.name = "p1"; one.channel_id = 1
    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=False)):
        pdb.get_all_from_hunt.return_value = [one]
        rdb.get_all.return_value = []
        failed = await svc.delete_hunt(guild, hunt)
    # A puzzle whose channel could not be deleted surfaces in the returned list.
    assert "p1" in failed


@pytest.mark.asyncio
async def test_delete_hunt_deletes_group_chat_channel():
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()

    group = MagicMock(id=9, category_id=100)
    group.has_target = False
    category = MagicMock(id=100)
    category.channels = []
    category.delete = AsyncMock()
    guild.categories = [category]
    chat_ch = MagicMock(id=555)

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)) as safe_delete, \
         patch("bot.core.puzzle_service.category_layout.group_chat_channel",
               return_value=chat_ch):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = [group]
        await svc.delete_hunt(guild, hunt)

    safe_delete.assert_any_call(guild, 555)

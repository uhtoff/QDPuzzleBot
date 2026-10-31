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
    h.solved_sheet_id = "ARCH_ID"
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
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)), \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)) as safe_delete, \
         patch("bot.core.puzzle_service.category_layout.group_chat_channel",
               return_value=chat_ch):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = [group]
        tgcdb.get_for_group.return_value = []
        await svc.delete_hunt(guild, hunt)

    safe_delete.assert_any_call(guild, 555)


@pytest.mark.asyncio
async def test_delete_hunt_tears_down_split_round_categories():
    """A split round (one that outgrew a single category into separate
    unsolved/solved/overflow categories) must have ALL its categories deleted
    by delete_hunt, not just tag_group.category_id — mirrors delete_round's
    existing use of category_layout.delete_group_categories (review #32)."""
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()

    group = MagicMock(id=9, category_id=100)
    guild.categories = []

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=True)) as delete_group_cats, \
         patch("bot.core.puzzle_service.category_layout.group_chat_channel",
               return_value=None):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = [group]

        await svc.delete_hunt(guild, hunt)

    delete_group_cats.assert_any_await(guild, group, tgcdb)


@pytest.mark.asyncio
async def test_delete_hunt_tears_down_categories_before_deleting_group_row():
    """tag_group_category rows FK-cascade off the tag_group row (migration 022),
    so TagGroupJsonDb.delete(tag_group.id) must run AFTER delete_group_categories
    reads them — deleting the row first would silently wipe the very rows
    delete_group_categories needs, making it fall back to (and only tear down)
    the legacy single-category path and orphan the solved/overflow categories."""
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()

    group = MagicMock(id=9, category_id=100)
    guild.categories = []

    manager = MagicMock()
    delete_group_cats = AsyncMock(return_value=True)
    manager.attach_mock(delete_group_cats, "delete_group_categories")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb"), \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               delete_group_cats), \
         patch("bot.core.puzzle_service.category_layout.group_chat_channel",
               return_value=None):
        manager.attach_mock(rdb.delete, "tag_group_delete")
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = [group]

        await svc.delete_hunt(guild, hunt)

    call_names = [c[0] for c in manager.mock_calls]
    assert call_names.index("delete_group_categories") < call_names.index("tag_group_delete")


@pytest.mark.asyncio
async def test_delete_hunt_removes_the_hunts_media_files(tmp_path):
    """The `media` rows cascade away with the hunt row, and media_gc only ever
    reclaims *from rows* — so unless the files go here, nothing can ever find
    them again. Both trees are hunt-keyed, so this is two rmtrees."""
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    uploads = tmp_path / "uploads" / "7"
    tiles = tmp_path / "tiles" / "7"
    uploads.mkdir(parents=True); (uploads / "a.png").write_bytes(b"x")
    tiles.mkdir(parents=True); (tiles / "0_0.png").write_bytes(b"x")
    # Another hunt's media must survive.
    other = tmp_path / "uploads" / "8"
    other.mkdir(parents=True); (other / "keep.png").write_bytes(b"x")

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.media_gc.media_root", return_value=tmp_path), \
         patch.object(svc, "_gsheet_cog", return_value=None):
        pdb.get_all_from_hunt.return_value = []
        rdb.get_all.return_value = []
        await svc.delete_hunt(guild, hunt)

    assert not uploads.exists()
    assert not tiles.exists()
    assert (other / "keep.png").exists()


@pytest.mark.asyncio
async def test_delete_hunt_does_not_delete_per_puzzle_tabs():
    """The whole Drive file goes at the end of delete_hunt, so deleting each
    puzzle's TAB first is pure waste — N serialised Sheets batchUpdates (all
    each a Sheets round trip) for tabs that are about to vanish
    with the file (review M20).

    Both files are deleted wholesale, and a puzzle's tab lives in exactly one of
    them (the archive iff on_solved_sheet, which is only ever set when the hunt
    has a solved sheet), so no tab survives. The puzzle's DB row and Discord
    channel are still torn down per puzzle — only the sheet call goes.
    """
    svc, guild, hunt = _delete_service(), _delete_guild(), _delete_hunt_obj()
    gsheet = MagicMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_hunt_spreadsheet = AsyncMock()

    p1 = MagicMock(id=101, channel_id=11); p1.name = "p1"; p1.on_solved_sheet = False
    p2 = MagicMock(id=102, channel_id=12); p2.name = "p2"; p2.on_solved_sheet = True
    p3 = MagicMock(id=103, channel_id=13); p3.name = "p3"; p3.on_solved_sheet = False

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet), \
         patch.object(svc, "_safe_delete_channel", AsyncMock(return_value=True)) as safe_delete:
        pdb.get_all_from_hunt.return_value = [p1, p2, p3]
        rdb.get_all.return_value = []
        await svc.delete_hunt(guild, hunt)

    # No per-tab deletes at all — not one per puzzle, not one total.
    gsheet.delete_puzzle_spreadsheet.assert_not_awaited()

    # Both whole files still go, so every tab (working AND archive) is removed.
    called_ids = [c.args[0] for c in gsheet.delete_hunt_spreadsheet.call_args_list]
    assert called_ids.count("SHEET_ID") == 1
    assert called_ids.count("ARCH_ID") == 1

    # The rest of the per-puzzle teardown is untouched.
    assert [c.args[0] for c in pdb.delete.call_args_list] == [101, 102, 103]
    # 11/12/13 are the three puzzle channels; 60 is the hunt channel deleted last.
    assert [c.args[1] for c in safe_delete.call_args_list] == [11, 12, 13, 60]

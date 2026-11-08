"""Hunt archive / hunt delete / round delete remove the hunt's voice channels
and its "<hunt> Voice[ N]" categories (final review C1, I1, I2)."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hunt_archive, voice
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_hunt, make_puzzle, make_round


def _service():
    bot = MagicMock()
    bot.application_id = 999
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def _hunt():
    h = HuntData()
    h.id = 7
    h.name = "H"
    h.category_id = 50
    h.channel_id = 60
    h.role_id = 0
    h.start_time = None
    h.voice_channel_id = 12
    return h


def _voice_channel(id, name=None):
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id = id
    ch.name = name or f"voice{id}"
    ch.delete = AsyncMock()
    return ch


def _text_channel(id, name=None):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = id
    ch.name = name or f"text{id}"
    ch.delete = AsyncMock()
    return ch


def _cat(id, name, channels):
    c = MagicMock()
    c.id = id
    c.name = name
    c.channels = list(channels)
    c.delete = AsyncMock()
    return c


def test_is_voice_category_name_is_public_and_strict():
    assert voice.is_voice_category_name("H", "H Voice")
    assert voice.is_voice_category_name("H", "H Voice 3")
    assert not voice.is_voice_category_name("H", "H Voice x")
    assert not voice.is_voice_category_name("H", "Other Voice")
    assert not voice.is_voice_category_name("H", MagicMock())  # never a Mock match


def test_hunt_voice_categories_ignores_a_same_named_category_holding_text():
    """A *hunt* called "H Voice" has a main category of that name full of text
    channels: it must never be mistaken for hunt "H"'s voice category."""
    real = _cat(80, "H Voice 2", [_voice_channel(81)])
    impostor = _cat(90, "H Voice", [_text_channel(91)])
    guild = MagicMock()
    guild.categories = [real, impostor]
    assert voice.hunt_voice_categories(guild, _hunt()) == [real]


@pytest.fixture(autouse=True)
def _no_archive_category_lookup():
    with patch("bot.core.hunt_archive.place_in_archive_category", new=AsyncMock()):
        yield


async def test_archive_hunt_sweeps_voice_categories():
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"
    dest = MagicMock(); dest.id = 60; dest.threads = []; dest.edit = AsyncMock()
    main_cat = _cat(50, "H", [dest])
    dest.category = main_cat
    pvc = _voice_channel(21, "Cap One")
    vcat = _cat(80, "H Voice", [pvc])
    vcat2 = _cat(85, "H Voice 2", [])
    guild = MagicMock()
    guild.categories = [main_cat, vcat, vcat2]
    guild.get_channel.return_value = dest

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch("bot.core.hunt_archive.archive_and_delete_channel",
               new=AsyncMock(return_value=hunt_archive.ChannelArchiveResult(messages_moved=0))), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)) as safe_del, \
         patch.object(svc, "safe_rename", new=AsyncMock()):
        tgdb.get_all.return_value = []
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []
        report = await svc.archive_hunt(guild, hunt, invoker)

    safe_del.assert_any_await(guild, 21)
    vcat.delete.assert_awaited_once()
    vcat2.delete.assert_awaited_once()
    assert report.voice_deleted == 1


async def test_delete_hunt_removes_puzzle_and_hunt_voice_and_voice_categories():
    svc, hunt = _service(), _hunt()
    pvc = _voice_channel(21, "Cap One")
    orphan = _voice_channel(22, "Orphan")
    vcat = _cat(80, "H Voice", [pvc, orphan])
    by_id = {21: pvc, 22: orphan}
    guild = MagicMock()
    guild.categories = [vcat]
    guild.get_channel = MagicMock(side_effect=lambda i: by_id.get(i))
    puzzle = make_puzzle(id=3, channel_id=31)
    puzzle.voice_channel_id = 21

    with patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb"), \
         patch("bot.core.puzzle_service.media_gc.remove_hunt_media"), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=True)), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)) as safe_del:
        pdb.get_all_from_hunt.return_value = [puzzle]
        rdb.get_all.return_value = []
        await svc.delete_hunt(guild, hunt)

    pvc.delete.assert_awaited()                       # puzzle's voice channel
    safe_del.assert_any_await(guild, 12)              # hunt voice channel
    safe_del.assert_any_await(guild, 22)              # orphan in the voice category
    vcat.delete.assert_awaited_once()                 # the voice category itself


async def test_delete_round_deletes_member_puzzle_voice():
    import bot.store as store
    svc = _service()
    hunt = make_hunt(category_id=500)
    tag_group = make_round(id=5, category_id=100, target_puzzle_id=1)
    target = make_puzzle(id=1, is_target=True, channel_id=901, tags=[5])
    target.voice_channel_id = 951
    target_vc = _voice_channel(951)
    target_ch = MagicMock(id=901); target_ch.delete = AsyncMock()
    round_cat = MagicMock(id=100); round_cat.channels = [target_ch]; round_cat.delete = AsyncMock()
    guild = MagicMock()
    guild.categories = [round_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {901: target_ch, 951: target_vc}.get(cid))
    guild.get_channel_or_thread = guild.get_channel

    with patch.object(store, "TagGroupCategoryDb", MagicMock()), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = [target]
        pdb.get_children.return_value = []
        pdb.get_by_attr.return_value = None
        tgcdb.get_for_group.return_value = []
        await svc.delete_round(guild, hunt, tag_group)

    target_vc.delete.assert_awaited_once()

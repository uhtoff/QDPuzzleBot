"""PuzzleService.archive_hunt — whole-hunt sweep orchestration.

The per-channel routine (hunt_archive.archive_and_delete_channel) is patched out;
this asserts enumeration, voice deletion, category teardown, rename, and the
archived flag.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import discord

from bot.core import hunt_archive
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData



@pytest.fixture(autouse=True)
def _no_archive_category_lookup():
    """Archive-category placement reads the server's settings row; it has its
    own tests (test_archive_category.py), so keep these off the database."""
    with patch("bot.core.hunt_archive.place_in_archive_category", new=AsyncMock()):
        yield

def _service():
    bot = MagicMock()
    bot.application_id = 999
    return PuzzleService(bot)


def _hunt():
    h = HuntData()
    h.id = 7
    h.name = "H"
    h.category_id = 50
    h.channel_id = 60
    h.role_id = 0            # -> team falls back to invoker name
    h.start_time = None      # -> name has no YYYY-MM prefix
    return h


def _text_channel(id):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = id
    ch.name = f"text{id}"
    return ch


def _voice_channel(id):
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id = id
    ch.name = f"voice{id}"
    return ch


async def test_archive_hunt_sweeps_renames_and_marks_archived():
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"

    dest = MagicMock(); dest.id = 60; dest.threads = []
    dest.edit = AsyncMock()
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [dest]; main_cat.delete = AsyncMock()
    dest.category = main_cat
    text = _text_channel(71); voice = _voice_channel(72)
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [text, voice]; round_cat.delete = AsyncMock()

    guild = MagicMock()
    guild.categories = [main_cat, round_cat]
    guild.get_channel.return_value = dest
    # discord.utils.get(guild.categories, id=cid) / name=... resolves against this list.

    tg = MagicMock(); tg.id = 3; tg.category_id = 70

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch("bot.core.hunt_archive.archive_and_delete_channel",
               new=AsyncMock(return_value=hunt_archive.ChannelArchiveResult(messages_moved=3))) as archive_call, \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)) as safe_del, \
         patch.object(svc, "safe_rename", new=AsyncMock()) as rename, \
         patch("bot.core.hunt_archive.place_in_archive_category", new=AsyncMock()) as place:
        tgdb.get_all.return_value = [tg]
        tgcdb.get_for_group.return_value = []       # single-category (legacy) round
        pdb.get_by_attr.return_value = None         # thread named after channel

        report = await svc.archive_hunt(guild, hunt, invoker)

    # Text channel archived; voice channel deleted.
    archive_call.assert_awaited_once()
    assert archive_call.await_args.args[3] is text        # (bot, dest, webhook, source)
    safe_del.assert_any_await(guild, 72)
    # Round category deleted; dest detached and main category deleted.
    round_cat.delete.assert_awaited_once()
    dest.edit.assert_awaited_once_with(category=None)
    main_cat.delete.assert_awaited_once()
    # Renamed and flagged.
    rename.assert_awaited_once_with(dest, "H - me")
    hdb.commit.assert_called_once_with(hunt)
    # ...and moved to the top of this server's archive category (if it has one).
    place.assert_awaited_once_with(guild, dest)
    assert hunt.archived is True
    assert report.final_name == "H - me"
    assert report.channels_archived == 1
    assert report.voice_deleted == 1


async def test_archive_hunt_isolates_a_failing_channel():
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"

    dest = MagicMock(); dest.id = 60; dest.threads = []; dest.edit = AsyncMock()
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [dest]; main_cat.delete = AsyncMock()
    dest.category = main_cat
    bad = _text_channel(71)
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [bad]; round_cat.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [main_cat, round_cat]; guild.get_channel.return_value = dest
    tg = MagicMock(); tg.id = 3; tg.category_id = 70

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch("bot.core.hunt_archive.archive_and_delete_channel",
               new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)), \
         patch.object(svc, "safe_rename", new=AsyncMock()):
        tgdb.get_all.return_value = [tg]
        tgcdb.get_for_group.return_value = []
        pdb.get_by_attr.return_value = None
        report = await svc.archive_hunt(guild, hunt, invoker)

    assert "text71" in report.failures       # failure recorded, run still completed
    assert hunt.archived is True


async def test_archive_hunt_records_failure_when_safe_delete_returns_false():
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"

    dest = MagicMock(); dest.id = 60; dest.threads = []; dest.edit = AsyncMock()
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [dest]; main_cat.delete = AsyncMock()
    dest.category = main_cat
    voice = _voice_channel(72)
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [voice]; round_cat.delete = AsyncMock()
    guild = MagicMock(); guild.categories = [main_cat, round_cat]; guild.get_channel.return_value = dest
    tg = MagicMock(); tg.id = 3; tg.category_id = 70

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.hunt_archive.PuzzleJsonDb") as pdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=False)), \
         patch.object(svc, "safe_rename", new=AsyncMock()):
        tgdb.get_all.return_value = [tg]
        tgcdb.get_for_group.return_value = []
        pdb.get_by_attr.return_value = None
        report = await svc.archive_hunt(guild, hunt, invoker)

    # Failed deletion is reported, not silently counted as a success.
    assert "voice72" in report.failures
    assert report.voice_deleted == 0
    assert hunt.archived is True

async def test_archive_hunt_drops_layout_join_rows():
    """Archive keeps every hunt/tag_group DB row (hunt.archived=True only), so
    no FK cascade ever fires — the split-layout join rows must be dropped
    explicitly, or they point at deleted categories forever and is_split()
    stays true for the archived hunt and every archived round."""
    svc, hunt = _service(), _hunt()
    invoker = MagicMock(); invoker.display_name = "me"

    dest = MagicMock(); dest.id = 60; dest.threads = []
    dest.edit = AsyncMock()
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [dest]; main_cat.delete = AsyncMock()
    dest.category = main_cat
    guild = MagicMock()
    guild.categories = [main_cat]
    guild.get_channel.return_value = dest
    tg = MagicMock(); tg.id = 3; tg.category_id = 0

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as tgdb, \
         patch("bot.core.puzzle_service.HuntJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb") as tgcdb, \
         patch("bot.core.puzzle_service.HuntCategoryDb") as hcdb, \
         patch("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())), \
         patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True)), \
         patch.object(svc, "safe_rename", new=AsyncMock()):
        tgdb.get_all.return_value = [tg]
        tgcdb.get_for_group.return_value = []
        hcdb.get_for_group.return_value = []

        await svc.archive_hunt(guild, hunt, invoker)

    hcdb.delete_for_group.assert_called_once_with(7)
    tgcdb.delete_for_group.assert_called_once_with(3)

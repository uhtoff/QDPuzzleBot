"""Forum-backed hunt option: per-hunt layout_mode + forum create/delete branches.

Mirrors tests/test_create_service.py wiring (service calls the *module* functions
bot.core.puzzle_service.get_or_create_channel / .create_forum_post and the relocated
PuzzleJsonDb / TagGroupJsonDb). Helpers are uniquely named + local to this file.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round


def _forum_svc():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot)


def _forum_hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.category_id = 1          # the single hunt category (forum parent)
    h.name = "H"
    h.layout_mode = "forum"    # field added in Phase 2; setattr works pre-field
    return h


def test_hunt_data_defaults_layout_mode_channel():
    # Right-reason failure pre-Phase-2: AttributeError (field does not exist yet).
    assert HuntData().layout_mode == "channel"


@pytest.mark.asyncio
async def test_create_tag_group_forum_creates_forum_under_hunt_category():
    svc = _forum_svc()
    hunt_category = MagicMock(id=1, position=0)
    hunt_category.name = "H"
    hunt_category.clone = AsyncMock(return_value=MagicMock(id=2, channels=[], edit=AsyncMock()))
    guild = MagicMock()
    guild.categories = [hunt_category]
    guild.channels = []
    guild.create_forum = AsyncMock(return_value=MagicMock(id=777))
    notifier = MagicMock(); notifier.welcome = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "G1"
        # meta_puzzle_name=False → Metaless round, so create_puzzle is not invoked.
        new_round, puzzle = await svc.create_tag_group(
            guild, _forum_hunt(), "New Round", notifier=notifier, meta_puzzle_name=False,
        )

    guild.create_forum.assert_awaited_once()        # forum branch taken
    hunt_category.clone.assert_not_awaited()         # channel-mode clone NOT used
    assert new_round.category_id == 777              # forum id stored in category_id


@pytest.mark.asyncio
async def test_create_puzzle_forum_creates_post_not_text_channel():
    svc = _forum_svc()
    forum = MagicMock(id=777)
    guild = MagicMock()
    guild.channels = []
    guild.categories = []
    guild.get_channel = MagicMock(return_value=forum)   # resolves the round's forum
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=777)        # category_id == forum id

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.create_forum_post",
               AsyncMock(return_value=MagicMock(id=222, mention="<#222>", name="newpuz"))) as mkpost, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock()) as pick, \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()) as goc:
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        result = await svc.create_puzzle(
            guild, _forum_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier,
        )

    mkpost.assert_awaited_once()          # forum post created
    pick.assert_not_awaited()             # category overflow layout NOT used
    goc.assert_not_awaited()              # text-channel creation NOT used
    assert result.channel_id == 222       # thread id stored in channel_id


@pytest.mark.asyncio
async def test_delete_round_forum_deletes_forum_channel():
    svc = _forum_svc()
    forum = MagicMock(id=777); forum.delete = AsyncMock()
    guild = MagicMock()
    guild.categories = []                                  # forum is NOT a category
    guild.get_channel = MagicMock(return_value=forum)
    tag_group = make_round(id=5, category_id=777)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = []       # no puzzles
        await svc.delete_round(guild, _forum_hunt(), tag_group)

    forum.delete.assert_awaited_once()                     # forum torn down


def test_forum_meta_thread_classifies_as_group_type_not_puzzle():
    """A forum round's target puzzle lives in a thread; cog_before_invoke must
    upgrade its channel_type to the group's type (so !info treats it as a meta),
    mirroring the category path. Regression: the thread branch hardcoded 'Puzzle'
    and never checked target_puzzle_id, so forum metas showed as plain puzzles on
    both the bang and web routes."""
    import asyncio
    import discord

    from bot.cogs.puzzles import Puzzles
    from tests.conftest import make_hunt, make_puzzle

    meta = make_puzzle(id=5, hunt_id=10, channel_id=777, is_target=True, tags=[1])
    hunt = make_hunt(id=10)
    group = make_round(id=1, target_puzzle_id=5)
    group.type = "Metapuzzle"

    thread = MagicMock(spec=discord.Thread)
    thread.id = 777
    ctx = MagicMock()
    ctx.channel = thread
    ctx.guild.id = 1
    ctx.message.id = 999

    bot = MagicMock()
    bot.get_cog.return_value = None
    cog = Puzzles(bot)

    with patch("bot.cogs.puzzles.GuildSettingsDb") as GuildDb, \
         patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb, \
         patch("bot.cogs.puzzles.HuntJsonDb") as HuntDb, \
         patch("bot.cogs.puzzles.TagGroupJsonDb") as RoundDb:
        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = None
        PuzzleDb.get_by_attr.return_value = meta
        HuntDb.get_by_attr.return_value = hunt
        RoundDb.get_by_attr.return_value = group   # target_puzzle_id lookup hits
        asyncio.run(cog.cog_before_invoke(ctx))

    env = cog.environment[999]
    assert env["channel_type"] == "Metapuzzle"   # upgraded, not 'Puzzle'
    assert env["tag_group"] is group
    assert env["puzzle"] is meta


# ---------------------------------------------------------------------------
# Phase 1 (1A): Group general-channel group type + service-owned welcomes.
# These FAIL until Phase 2 implements the Group path in create_tag_group.
# ---------------------------------------------------------------------------


def _channel_hunt():
    """Copy of _forum_hunt but channel-mode (category-clone layout)."""
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.category_id = 1
    h.name = "H"
    h.layout_mode = "channel"
    return h


@pytest.mark.asyncio
async def test_group_forum_mode_creates_plain_text_channel_no_forum():
    svc = _forum_svc()
    hunt_category = MagicMock(id=1, position=0); hunt_category.name = "H"
    guild = MagicMock(); guild.categories = [hunt_category]; guild.channels = []
    guild.create_forum = AsyncMock()                       # must NOT be called for a Group
    text_channel = MagicMock(id=4242); text_channel.mention = "<#4242>"; text_channel.name = "grp"
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(text_channel, True))):
        rdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "G1"
        new_round, puzzle = await svc.create_tag_group(
            guild, _forum_hunt(), "Grp", notifier=notifier,
            meta_puzzle_name=False, round_type="Group",
        )
    guild.create_forum.assert_not_awaited()                 # no forum for a Group
    assert puzzle is None                                    # no target puzzle
    pdb.commit.assert_not_called()                           # no PuzzleData
    assert new_round.category_id == 4242                     # group channel id stored
    notifier.welcome.assert_awaited_once()                  # group welcome posted
    # welcome posted for the GROUP (no puzzle):
    assert notifier.welcome.await_args.kwargs.get("puzzle") is None or \
           notifier.welcome.await_args.args[3] is None


@pytest.mark.asyncio
async def test_group_channel_mode_clones_category_and_adds_one_channel():
    svc = _forum_svc()
    hunt_category = MagicMock(id=1, position=0); hunt_category.name = "H"
    cloned = MagicMock(id=2, position=1, channels=[], edit=AsyncMock())
    hunt_category.clone = AsyncMock(return_value=cloned)
    guild = MagicMock(); guild.categories = [hunt_category]; guild.channels = []
    guild.create_forum = AsyncMock()                       # must NOT be called for a Group
    text_channel = MagicMock(id=4242); text_channel.mention = "<#4242>"; text_channel.name = "grp"
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(text_channel, True))):
        rdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "G1"
        new_round, puzzle = await svc.create_tag_group(
            guild, _channel_hunt(), "Grp", notifier=notifier,
            meta_puzzle_name=False, round_type="Group",
        )
    hunt_category.clone.assert_awaited_once()                # channel-mode clone used
    guild.create_forum.assert_not_awaited()                 # no forum for a Group
    pdb.commit.assert_not_called()                           # no PuzzleData
    assert puzzle is None
    notifier.welcome.assert_awaited_once()                  # group welcome posted


@pytest.mark.asyncio
async def test_meta_welcome_has_populated_group_code():
    # Regression for the ordering bug: group_code must be set BEFORE the welcome fires.
    svc = _forum_svc()
    hunt_category = MagicMock(id=1, position=0); hunt_category.name = "H"
    forum = MagicMock(id=777)
    guild = MagicMock(); guild.categories = [hunt_category]; guild.channels = []
    guild.create_forum = AsyncMock(return_value=forum)
    guild.get_channel = MagicMock(return_value=forum)       # create_puzzle resolves the forum
    seen = {}
    notifier = MagicMock()

    async def _cap(*a, **k):
        tg = k.get("tag_group") or a[2]
        seen["code"] = tg.group_code
    notifier.welcome = AsyncMock(side_effect=_cap)

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.create_forum_post",
               AsyncMock(return_value=MagicMock(id=222, mention="<#222>", name="m"))), \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category", AsyncMock()), \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        rdb.generate_uid.return_value = "GC9"
        await svc.create_tag_group(
            guild, _forum_hunt(), "M", notifier=notifier,
            meta_puzzle_name="M", round_type="Metapuzzle",
        )

    assert seen["code"] == "GC9"        # NOT None/'0' — proves group_code set before welcome

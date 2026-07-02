"""Phase 1 (1D): forum configuration — rules/tags/emoji on creation + guidelines sync.

Local, uniquely-named helpers (the parallel runner shards by file). These FAIL until
Phase 3: bot/core/forum_config.py does not exist and guild.create_forum is called with
only name=/category= (no available_tags / default_reaction_emoji / topic).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _cfg_svc():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot)


def _cfg_forum_hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.solved_sheet_id = "A1"
    h.url = None
    h.category_id = 1
    h.name = "H"
    h.layout_mode = "forum"
    return h


_EXPECTED_TAGS = {"solved", "meta", "capstone", "metameta", "runaround", "minimeta"}


def _emoji_is_thumbsup(value):
    if isinstance(value, str):
        return "👍" in value
    return getattr(value, "name", None) == "👍"


@pytest.mark.asyncio
async def test_create_forum_sets_tags_emoji_and_topic():
    svc = _cfg_svc()
    hunt_category = MagicMock(id=1, position=0); hunt_category.name = "H"
    guild = MagicMock(); guild.categories = [hunt_category]; guild.channels = []
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
        await svc.create_tag_group(
            guild, _cfg_forum_hunt(), "New Round", notifier=notifier, meta_puzzle_name=False,
        )

    kwargs = guild.create_forum.call_args.kwargs
    tags = kwargs.get("available_tags")
    assert isinstance(tags, list) and len(tags) == 6
    assert {t.name for t in tags} == _EXPECTED_TAGS
    assert _emoji_is_thumbsup(kwargs.get("default_reaction_emoji"))
    topic = kwargs.get("topic")
    assert isinstance(topic, str) and topic


@pytest.mark.asyncio
async def test_create_forum_syncs_guidelines_after_group_code_set():
    # The forum is created with a placeholder topic before the row exists; after the
    # group_code is populated the service must sync the real guidelines onto the forum.
    svc = _cfg_svc()
    hunt_category = MagicMock(id=1, position=0); hunt_category.name = "H"
    forum = MagicMock(id=777); forum.topic = "placeholder"; forum.edit = AsyncMock()
    guild = MagicMock(); guild.categories = [hunt_category]; guild.channels = []
    guild.create_forum = AsyncMock(return_value=forum)
    guild.get_channel.return_value = forum
    notifier = MagicMock(); notifier.welcome = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "G1"
        await svc.create_tag_group(
            guild, _cfg_forum_hunt(), "New Round", notifier=notifier, meta_puzzle_name=False,
        )

    forum.edit.assert_awaited()
    topic = forum.edit.await_args.kwargs.get("topic")
    assert isinstance(topic, str) and "New Round" in topic
    assert topic != "placeholder"


def test_render_welcome_plaintext_group():
    from bot.core.forum_config import render_welcome_plaintext

    group = make_round(id=1, name="MyGroup", group_code="GC9")
    group.type = "Group"
    out = render_welcome_plaintext("group", group, None, _cfg_forum_hunt())
    assert "MyGroup" in out
    assert "GC9" in out
    assert "!" in out                 # a bang-command bullet
    assert "Embed" not in out         # plain markdown, not an embed


def test_render_welcome_plaintext_meta():
    from bot.core.forum_config import render_welcome_plaintext

    group = make_round(id=1, name="MyMeta", group_code="GC9", target_puzzle_id=9)
    group.type = "Metapuzzle"
    puzzle = make_puzzle(id=9, name="MyMeta", is_target=True)
    out = render_welcome_plaintext("meta", group, puzzle, _cfg_forum_hunt())
    assert "MyMeta" in out
    assert "Embed" not in out


def test_render_welcome_plaintext_capstone():
    from bot.core.forum_config import render_welcome_plaintext

    group = make_round(id=1, name="MyCap", group_code="GC9", target_puzzle_id=9)
    group.type = "Capstone"
    puzzle = make_puzzle(id=9, name="MyCap", is_target=True)
    out = render_welcome_plaintext("capstone", group, puzzle, _cfg_forum_hunt())
    assert "MyCap" in out
    assert "Embed" not in out


@pytest.mark.asyncio
async def test_sync_forum_guidelines_edits_topic_when_changed():
    from bot.core.forum_config import sync_forum_guidelines

    group = make_round(id=1, name="MyGroup", group_code="GC9", category_id=777)
    group.type = "Group"
    hunt = _cfg_forum_hunt()
    forum = MagicMock(id=777); forum.topic = "old"; forum.edit = AsyncMock()
    guild = MagicMock(); guild.get_channel.return_value = forum

    await sync_forum_guidelines(guild, hunt, group, None)

    forum.edit.assert_awaited_once()
    assert "topic" in forum.edit.await_args.kwargs


@pytest.mark.asyncio
async def test_sync_forum_guidelines_noop_when_unchanged():
    from bot.core.forum_config import render_welcome_plaintext, sync_forum_guidelines

    group = make_round(id=1, name="MyGroup", group_code="GC9", category_id=777)
    group.type = "Group"
    hunt = _cfg_forum_hunt()
    rendered = render_welcome_plaintext("group", group, None, hunt)
    forum = MagicMock(id=777); forum.topic = rendered; forum.edit = AsyncMock()
    guild = MagicMock(); guild.get_channel.return_value = forum

    await sync_forum_guidelines(guild, hunt, group, None)

    forum.edit.assert_not_awaited()

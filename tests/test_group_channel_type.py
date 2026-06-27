"""Phase 1 (1B): Group channel classification + !info dispatch.

Mirrors tests/test_minimeta_service.py::TestCogBeforeInvokeThread (plain ``def``,
``asyncio.run``, patch the DAOs at the bot.cogs.puzzles import site). Helpers are
uniquely named + local to this file (the parallel runner shards by file).

These FAIL until Phase 2:
  - the cog 'Group' branch looks up only by ctx.channel.category.id (not channel.id);
  - !info has no Group branch and ``send_initial_group_channel_messages`` does not exist.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_round


def _grp_text_ctx(channel_id, category_id):
    """A plain (non-thread) text-channel ctx with its own id and a parent category id."""
    ctx = MagicMock()
    ctx.channel = MagicMock()            # NOT spec=discord.Thread -> isinstance Thread is False
    ctx.channel.id = channel_id
    ctx.channel.category.id = category_id
    ctx.guild.id = 1
    ctx.message.id = 999
    return ctx


def test_forum_group_channel_classifies_as_group_by_channel_id():
    # A forum-mode group's general channel is a normal text channel whose OWN id is
    # the group's category_id (no parent category match).
    import asyncio
    from bot.cogs.puzzles import Puzzles

    group = make_round(id=1, category_id=4242)
    group.type = "Group"
    ctx = _grp_text_ctx(channel_id=4242, category_id=1)

    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)

    with patch("bot.cogs.puzzles.GuildSettingsDb") as GuildDb, \
         patch("bot.cogs.puzzles.PuzzleJsonDb"), \
         patch("bot.cogs.puzzles.HuntJsonDb") as HuntDb, \
         patch("bot.cogs.puzzles.TagGroupJsonDb") as RoundDb:
        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = "Group"          # channel's own id is 'Group'
        HuntDb.get_by_attr.return_value = MagicMock()
        # The group's category_id == ctx.channel.id (4242); the parent category (1) misses.
        RoundDb.get_by_attr.side_effect = \
            lambda **kw: group if kw.get("category_id") == 4242 else None
        asyncio.run(cog.cog_before_invoke(ctx))

    env = cog.environment[999]
    assert env["channel_type"] == "Group"
    assert env["tag_group"] is group        # proves lookup by ctx.channel.id


def test_channel_group_classifies_via_category():
    # channel-mode group: the general channel's .category.id is the group's category_id.
    import asyncio
    from bot.cogs.puzzles import Puzzles

    group = make_round(id=1, category_id=4242)
    group.type = "Group"
    ctx = _grp_text_ctx(channel_id=50, category_id=4242)

    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)

    with patch("bot.cogs.puzzles.GuildSettingsDb") as GuildDb, \
         patch("bot.cogs.puzzles.PuzzleJsonDb"), \
         patch("bot.cogs.puzzles.HuntJsonDb") as HuntDb, \
         patch("bot.cogs.puzzles.TagGroupJsonDb") as RoundDb:
        GuildDb.get.return_value = MagicMock()
        # channel's own id -> None; parent category id -> 'Group'.
        GuildDb.get_channel_type.side_effect = \
            lambda cid: "Group" if cid == 4242 else None
        HuntDb.get_by_attr.return_value = MagicMock()
        RoundDb.get_by_attr.side_effect = \
            lambda **kw: group if kw.get("category_id") == 4242 else None
        asyncio.run(cog.cog_before_invoke(ctx))

    assert cog.environment[999]["channel_type"] == "Group"


def test_legacy_metaless_round_type_routes_to_group():
    # A legacy group row with type == "Metaless Round" still resolves via the new
    # channel-id lookup (forum-group style).
    import asyncio
    from bot.cogs.puzzles import Puzzles

    group = make_round(id=1, category_id=4242)
    group.type = "Metaless Round"
    ctx = _grp_text_ctx(channel_id=4242, category_id=1)

    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)

    with patch("bot.cogs.puzzles.GuildSettingsDb") as GuildDb, \
         patch("bot.cogs.puzzles.PuzzleJsonDb"), \
         patch("bot.cogs.puzzles.HuntJsonDb") as HuntDb, \
         patch("bot.cogs.puzzles.TagGroupJsonDb") as RoundDb:
        GuildDb.get.return_value = MagicMock()
        GuildDb.get_channel_type.return_value = "Group"
        HuntDb.get_by_attr.return_value = MagicMock()
        RoundDb.get_by_attr.side_effect = \
            lambda **kw: group if kw.get("category_id") == 4242 else None
        asyncio.run(cog.cog_before_invoke(ctx))

    assert cog.environment[999]["tag_group"].type in ("Group", "Metaless Round")


# ---------------------------------------------------------------------------
# !info dispatch — Group / Metaless Round both route to the group handler.
# FAILS now: send_initial_group_channel_messages does not exist and !info has no
# Group branch (Group falls through to the ":x: not a hunt channel" else).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_info_group_calls_group_handler():
    from bot.cogs.puzzles import Puzzles

    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Group"
    cog.send_initial_group_channel_messages = AsyncMock()
    cog.send_initial_metaless_round_channel_messages = AsyncMock()
    ctx = make_ctx()

    await Puzzles.info.callback(cog, ctx)

    cog.send_initial_group_channel_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_info_metaless_round_calls_group_handler():
    from bot.cogs.puzzles import Puzzles

    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Metaless Round"
    cog.send_initial_group_channel_messages = AsyncMock()
    cog.send_initial_metaless_round_channel_messages = AsyncMock()
    ctx = make_ctx()

    await Puzzles.info.callback(cog, ctx)

    cog.send_initial_group_channel_messages.assert_awaited_once()

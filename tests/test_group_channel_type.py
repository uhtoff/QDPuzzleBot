"""Phase 1 (1B): Group channel classification + !info dispatch.

Mirrors tests/test_minimeta_service.py::TestCogBeforeInvokeThread (plain ``def``,
``asyncio.run``, patch the DAOs at the bot.cogs.puzzles import site). Helpers are
uniquely named + local to this file (the parallel runner shards by file).
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


# ---------------------------------------------------------------------------
# !info dispatch — a Group channel routes to the group welcome handler.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_info_group_calls_group_handler():
    from bot.cogs.puzzles import Puzzles

    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Group"
    cog.send_initial_group_channel_messages = AsyncMock()
    ctx = make_ctx()

    await Puzzles.info.callback(cog, ctx)

    cog.send_initial_group_channel_messages.assert_awaited_once()

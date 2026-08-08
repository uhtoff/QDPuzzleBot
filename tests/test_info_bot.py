"""!info_bot (2026-08-07 review): two template leftovers from the LadderSpot
bot this project was forked from -- the embed still announced itself as
LadderSpot, and it read ctx.guild.shard_id, which is None in a DM."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.utility import Utility


def _cog():
    cog = Utility.__new__(Utility)
    cog.bot = MagicMock()
    cog.bot.guilds = [MagicMock()]
    cog.bot.users = [MagicMock()]
    cog.bot.shard_count = 1
    cog.bot.version = "0.1.0"
    cog.bot.invite = "https://example.test/invite"
    cog.bot.user.avatar = None
    return cog


def _ctx(guild):
    ctx = MagicMock()
    ctx.guild = guild
    ctx.send = AsyncMock()
    return ctx


def _sent_embed(ctx):
    return ctx.send.await_args.kwargs["embed"]


def _all_text(embed):
    parts = [embed.title or "", embed.description or "", embed.footer.text or ""]
    parts += [f.name + " " + f.value for f in embed.fields]
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_info_bot_does_not_crash_in_a_dm():
    """ctx.guild is None outside a guild, so ctx.guild.shard_id raised
    AttributeError and the command died instead of answering."""
    ctx = _ctx(guild=None)
    await Utility.info_bot.callback(_cog(), ctx)
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_info_bot_reports_the_shard_in_a_guild():
    """The counterpart: the shard id must still be reported where there is one,
    or a fix that simply dropped the field would satisfy the DM test."""
    guild = MagicMock()
    guild.shard_id = 3
    ctx = _ctx(guild=guild)
    await Utility.info_bot.callback(_cog(), ctx)
    assert "Shard ID: 3" in _all_text(_sent_embed(ctx))


@pytest.mark.asyncio
async def test_info_bot_is_not_branded_as_the_upstream_bot():
    ctx = _ctx(guild=MagicMock())
    await Utility.info_bot.callback(_cog(), ctx)
    text = _all_text(_sent_embed(ctx))
    assert "LadderSpot" not in text
    assert "QDPuzzleBot" in text

"""Delete-path hardening tests (M10): None-guard + rate-limit tolerance."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.puzzles import Puzzles


def _cog():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)


def _guild_with_channel(channel_id, channel):
    guild = MagicMock()
    guild.channels = [channel] if channel is not None else []
    return guild


@pytest.mark.asyncio
async def test_safe_delete_missing_channel_returns_false_no_raise():
    cog = _cog()
    guild = _guild_with_channel(5, None)  # channel already gone
    ok = await cog._safe_delete_channel(guild, 5)
    assert ok is False


@pytest.mark.asyncio
async def test_safe_delete_existing_channel_calls_delete():
    cog = _cog()
    channel = SimpleNamespace(id=5, delete=AsyncMock())
    guild = _guild_with_channel(5, channel)
    ok = await cog._safe_delete_channel(guild, 5)
    assert ok is True
    channel.delete.assert_awaited_once_with(reason=cog.DELETE_REASON)


@pytest.mark.asyncio
async def test_safe_delete_swallows_ratelimited():
    cog = _cog()
    channel = SimpleNamespace(
        id=5,
        delete=AsyncMock(side_effect=discord.RateLimited(retry_after=5.0)),
    )
    guild = _guild_with_channel(5, channel)
    ok = await cog._safe_delete_channel(guild, 5)
    assert ok is False  # reported as failure, did not raise


@pytest.mark.asyncio
async def test_safe_delete_notfound_is_silent(caplog):
    """A channel deleted between lookup and delete (404) is benign — returns
    False without an exception-level log."""
    cog = _cog()
    channel = SimpleNamespace(
        id=5,
        delete=AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone")),
    )
    guild = _guild_with_channel(5, channel)
    with caplog.at_level("ERROR"):
        ok = await cog._safe_delete_channel(guild, 5)
    assert ok is False
    assert caplog.records == []  # no error log for the benign race

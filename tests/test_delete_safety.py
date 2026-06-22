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


@pytest.mark.asyncio
async def test_delete_hunt_continues_past_missing_channel(monkeypatch):
    """delete_hunt must not abort when one puzzle channel is already gone."""
    import bot.cogs.puzzles as puzzles_mod

    cog = _cog()
    # Two puzzles; channel for the first is missing, second present.
    p1 = SimpleNamespace(id=1, name="P1", channel_id=101)
    p2 = SimpleNamespace(id=2, name="P2", channel_id=102)
    present = SimpleNamespace(id=102, delete=AsyncMock())

    guild = MagicMock()
    guild.channels = [present]      # 101 missing, 102 present
    guild.categories = []

    hunt_channel = SimpleNamespace(delete=AsyncMock(), send=AsyncMock(),
                                   category=SimpleNamespace(delete=AsyncMock()))
    ctx = SimpleNamespace(message=SimpleNamespace(id=7), channel=hunt_channel)
    cog.environment[7] = {
        "channel_type": "Hunt", "hunt": SimpleNamespace(id=10),
        "hunt_round": None, "puzzle": None, "guild": guild, "guild_data": None,
        "channel": hunt_channel,
    }

    monkeypatch.setattr(puzzles_mod, "PuzzleJsonDb", MagicMock(
        get_all_from_hunt=MagicMock(return_value=[p1, p2]), delete=MagicMock()))
    monkeypatch.setattr(puzzles_mod, "RoundJsonDb", MagicMock(
        get_all=MagicMock(return_value=[])))
    monkeypatch.setattr(puzzles_mod, "HuntJsonDb", MagicMock(delete=MagicMock()))
    cog._confirm_destructive = AsyncMock(return_value=True)  # gate added in Task 4; harmless now
    cog.delete_puzzle_data = AsyncMock(return_value=True)

    # Should complete without raising despite channel 101 missing.
    await cog.delete_hunt.callback(cog, ctx)

    present.delete.assert_awaited_once()        # the present channel was deleted
    hunt_channel.delete.assert_awaited()        # reached the end (did not abort)

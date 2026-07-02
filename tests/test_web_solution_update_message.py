"""web_post_solution_update_embed must write to the puzzle channel like the
bang !partial/!change_solution/!add_solution commands.

Previously the web solve route (bot/web/actions.py) only posted an embed and
refreshed the pin for mode="full"; a dashboard partial/change/add mutated the
DB but left the puzzle channel silent and the pinned info stale (#62).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.puzzles import Puzzles


def _make_cog():
    cog = Puzzles.__new__(Puzzles)
    cog.web_refresh_info = AsyncMock()
    return cog


def _guild_with_channel(channel):
    guild = MagicMock()
    guild.id = 99
    guild.get_channel_or_thread.return_value = channel
    return guild


async def test_partial_posts_embed_and_refreshes_pin():
    cog = _make_cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = _guild_with_channel(channel)
    puzzle = MagicMock(channel_id=111, solution="BLUE")

    with patch("bot.cogs.puzzles.GuildSettingsDb") as gs:
        gs.get.return_value = MagicMock(discord_bot_emoji=":x:")
        await cog.web_post_solution_update_embed(guild, MagicMock(), puzzle, "partial")

    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == "Partially SOLVED!"
    assert "BLUE" in embed.description
    cog.web_refresh_info.assert_awaited_once()


async def test_change_posts_embed_and_refreshes_pin():
    cog = _make_cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = _guild_with_channel(channel)
    puzzle = MagicMock(channel_id=111, solution="WHALE")

    with patch("bot.cogs.puzzles.GuildSettingsDb") as gs:
        gs.get.return_value = MagicMock(discord_bot_emoji=":x:")
        await cog.web_post_solution_update_embed(guild, MagicMock(), puzzle, "change")

    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == "PUZZLE SOLUTION UPDATED!"
    assert "WHALE" in embed.description
    cog.web_refresh_info.assert_awaited_once()


async def test_add_posts_embed_and_refreshes_pin():
    cog = _make_cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = _guild_with_channel(channel)
    puzzle = MagicMock(channel_id=111, solution="BLUE/GREY")

    with patch("bot.cogs.puzzles.GuildSettingsDb") as gs:
        gs.get.return_value = MagicMock(discord_bot_emoji=":x:")
        await cog.web_post_solution_update_embed(guild, MagicMock(), puzzle, "add")

    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == "EXTRA PUZZLE SOLUTION ADDED!"
    assert "BLUE/GREY" in embed.description
    cog.web_refresh_info.assert_awaited_once()


async def test_mini_puzzle_thread_resolves_via_get_channel_or_thread():
    cog = _make_cog()
    thread = MagicMock(spec=discord.Thread)
    thread.send = AsyncMock()
    guild = _guild_with_channel(thread)
    puzzle = MagicMock(channel_id=222, solution="X")

    with patch("bot.cogs.puzzles.GuildSettingsDb") as gs:
        gs.get.return_value = MagicMock(discord_bot_emoji=":x:")
        await cog.web_post_solution_update_embed(guild, MagicMock(), puzzle, "change")

    thread.send.assert_awaited_once()
    cog.web_refresh_info.assert_awaited_once()

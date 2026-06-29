"""web_post_unsolved must write to the puzzle channel like the bang !unsolve.

Previously the web unsolve path only repositioned the channel and posted no
message, so an unsolve triggered from the dashboard was silent in Discord.
web_post_unsolved now posts an UNSOLVED embed (mirroring web_post_solved_embed)
and refreshes the pinned info, in addition to the reposition.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.puzzles import Puzzles


def _make_cog():
    cog = Puzzles.__new__(Puzzles)
    cog.web_refresh_info = AsyncMock()
    cog._apply_unsolved_move = AsyncMock()
    return cog


def _guild_with_channel(channel):
    guild = MagicMock()
    guild.id = 99
    guild.get_channel_or_thread.return_value = channel
    return guild


async def test_posts_embed_repositions_and_refreshes_for_a_channel():
    cog = _make_cog()
    channel = MagicMock()  # not a discord.Thread
    channel.category = MagicMock()
    channel.send = AsyncMock()
    guild = _guild_with_channel(channel)
    puzzle = MagicMock(channel_id=111)

    with patch("bot.cogs.puzzles.GuildSettingsDb") as gs:
        gs.get.return_value = MagicMock(discord_bot_emoji=":x:")
        await cog.web_post_unsolved(guild, MagicMock(), puzzle, prev_solution="ANSWER")

    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert "ANSWER" in embed.description
    cog._apply_unsolved_move.assert_awaited_once_with(guild, channel)
    cog.web_refresh_info.assert_awaited_once()


async def test_thread_gets_embed_and_refresh_but_no_reposition():
    cog = _make_cog()
    thread = MagicMock(spec=discord.Thread)
    thread.send = AsyncMock()
    guild = _guild_with_channel(thread)
    puzzle = MagicMock(channel_id=222)

    with patch("bot.cogs.puzzles.GuildSettingsDb") as gs:
        gs.get.return_value = MagicMock(discord_bot_emoji=":x:")
        await cog.web_post_unsolved(guild, MagicMock(), puzzle, prev_solution="ANSWER")

    thread.send.assert_awaited_once()
    cog._apply_unsolved_move.assert_not_awaited()
    cog.web_refresh_info.assert_awaited_once()

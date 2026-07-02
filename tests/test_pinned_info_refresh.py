"""#20: pin-refresh must not assume (await channel.pins())[-1] is the bot's own
info message — it must find the bot's pin by author and recover (re-send +
re-pin) rather than raise if it's missing or unpinnable."""
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.puzzles import Puzzles


def _make_cog(bot_user_id=999):
    cog = Puzzles.__new__(Puzzles)
    cog.bot = MagicMock()
    cog.bot.user.id = bot_user_id
    return cog


def _pin(author_id, msg_id):
    msg = MagicMock()
    msg.id = msg_id
    msg.author.id = author_id
    msg.edit = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_edits_the_bots_own_pin_not_just_the_last_one():
    """A user's message pinned after the bot's info message must not be edited
    instead of the bot's own pin — this is the >50-pins / not-last-anymore case."""
    cog = _make_cog(bot_user_id=999)
    bot_pin = _pin(author_id=999, msg_id=1)
    other_pin = _pin(author_id=111, msg_id=2)
    channel = MagicMock()
    channel.pins = AsyncMock(return_value=[bot_pin, other_pin])

    embed = MagicMock()
    await cog._refresh_pinned_info(channel, embed=embed)

    bot_pin.edit.assert_awaited_once_with(embed=embed)
    other_pin.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_recreates_pin_when_bots_message_is_gone():
    """If a mod unpinned/deleted the bot's info message, re-send + re-pin
    instead of raising IndexError on an empty/foreign pins list."""
    cog = _make_cog(bot_user_id=999)
    channel = MagicMock()
    channel.pins = AsyncMock(return_value=[])
    new_message = MagicMock()
    new_message.pin = AsyncMock()
    channel.send = AsyncMock(return_value=new_message)

    embed = MagicMock()
    result = await cog._refresh_pinned_info(channel, embed=embed)

    channel.send.assert_awaited_once_with(embed=embed)
    new_message.pin.assert_awaited_once()
    assert result is new_message


@pytest.mark.asyncio
async def test_recreates_pin_when_edit_forbidden():
    cog = _make_cog(bot_user_id=999)
    bot_pin = _pin(author_id=999, msg_id=1)
    bot_pin.edit = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no perms"))
    channel = MagicMock()
    channel.pins = AsyncMock(return_value=[bot_pin])
    new_message = MagicMock()
    new_message.pin = AsyncMock()
    channel.send = AsyncMock(return_value=new_message)

    embed = MagicMock()
    result = await cog._refresh_pinned_info(channel, embed=embed)

    channel.send.assert_awaited_once_with(embed=embed)
    assert result is new_message


@pytest.mark.asyncio
async def test_returns_none_when_recreate_also_forbidden():
    cog = _make_cog(bot_user_id=999)
    channel = MagicMock()
    channel.pins = AsyncMock(return_value=[])
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no perms"))

    result = await cog._refresh_pinned_info(channel, embed=MagicMock())

    assert result is None


@pytest.mark.asyncio
async def test_pins_fetch_forbidden_falls_back_to_recreate():
    cog = _make_cog(bot_user_id=999)
    channel = MagicMock()
    channel.pins = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no perms"))
    new_message = MagicMock()
    new_message.pin = AsyncMock()
    channel.send = AsyncMock(return_value=new_message)

    result = await cog._refresh_pinned_info(channel, embed=MagicMock())

    assert result is new_message


@pytest.mark.asyncio
async def test_embeds_variant_uses_embeds_kwarg():
    cog = _make_cog(bot_user_id=999)
    bot_pin = _pin(author_id=999, msg_id=1)
    channel = MagicMock()
    channel.pins = AsyncMock(return_value=[bot_pin])

    embeds = [MagicMock(), MagicMock()]
    await cog._refresh_pinned_info(channel, embeds=embeds)

    bot_pin.edit.assert_awaited_once_with(embeds=embeds)

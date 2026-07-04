"""Out-of-band Discord deletions (a user hand-deleting a puzzle channel or a
whole bot-managed category) hit none of the service-side consolidation
triggers — the cog's on_guild_channel_delete listener must catch them.
Bot-initiated deletions also fire it, but duplicate triggers are no-ops."""
from unittest.mock import MagicMock, patch

import discord
import pytest

from tests.conftest import make_cog


@pytest.mark.asyncio
async def test_deleted_channel_triggers_parent_category_consolidation():
    cog = make_cog()
    parent = MagicMock(id=201)
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    channel.category = parent

    with patch("bot.cogs.puzzles.category_layout") as cl:
        await cog.on_guild_channel_delete(channel)

    cl.maybe_consolidate_category.assert_called_once_with(channel.guild, parent)


@pytest.mark.asyncio
async def test_deleted_category_triggers_own_row_reap():
    """Deleting a whole tracked category leaves a dangling join row — trigger
    consolidation for the category itself so its owner gets reaped/refolded."""
    cog = make_cog()
    category = MagicMock(spec=discord.CategoryChannel)
    category.guild = MagicMock()

    with patch("bot.cogs.puzzles.category_layout") as cl:
        await cog.on_guild_channel_delete(category)

    cl.maybe_consolidate_category.assert_called_once_with(category.guild, category)


@pytest.mark.asyncio
async def test_deleted_uncategorized_channel_is_ignored():
    cog = make_cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    channel.category = None

    with patch("bot.cogs.puzzles.category_layout") as cl:
        await cog.on_guild_channel_delete(channel)

    cl.maybe_consolidate_category.assert_not_called()

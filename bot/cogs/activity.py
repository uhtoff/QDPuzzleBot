import datetime
import logging

import discord
from discord.ext import commands

from bot.store import ChannelActivityDb
from bot.web import events

logger = logging.getLogger(__name__)


class ChannelActivity(commands.Cog):
    """Records the most recent non-bot message per channel for the dashboard.

    The dashboard's "recent activity" view reads the channel_activity table so it
    never has to fetch channel history (slow + rate-limited) at request time.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only track real human messages in guild channels.
        if message.author.bot or message.guild is None:
            return
        try:
            author_name = getattr(message.author, "display_name", None) or message.author.name
            author_name = author_name[:255]
            ChannelActivityDb.record(
                channel_id=message.channel.id,
                guild_id=message.guild.id,
                at=message.created_at.replace(tzinfo=None),  # store naive UTC, as elsewhere
                author_id=message.author.id,
                author_name=author_name,
            )
            events.publish({
                "type": "activity",
                "channel_id": str(message.channel.id),
                "guild_id": message.guild.id,
                "at": message.created_at.replace(tzinfo=datetime.timezone.utc).isoformat(),
                "author": author_name,
            })
        except Exception:
            # Activity tracking must never break message handling.
            logger.exception("Failed to record channel activity")


async def setup(bot):
    await bot.add_cog(ChannelActivity(bot))

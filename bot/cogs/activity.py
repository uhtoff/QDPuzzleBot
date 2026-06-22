import asyncio
import datetime
import logging

import discord
from discord.ext import commands

from bot.store import ChannelActivityDb
from bot.web import events

logger = logging.getLogger(__name__)

# Cap the backlog so a wedged/slow DB can't grow the queue without bound. At ~one
# row per human message this is generous; if we ever hit it we drop the oldest
# pending writes (the table only stores the *latest* message per channel anyway).
_QUEUE_MAXSIZE = 10000


class ChannelActivity(commands.Cog):
    """Records the most recent non-bot message per channel for the dashboard.

    The dashboard's "recent activity" view reads the channel_activity table so it
    never has to fetch channel history (slow + rate-limited) at request time.

    The DB write is intentionally kept *off* the message-handling path: on_message
    only enqueues a record (and publishes the in-memory live event), and a single
    background worker drains the queue, running the blocking DB write in a thread
    via asyncio.to_thread. That way a slow/reconnecting MySQL server can no longer
    stall the whole bot's event loop on every message.
    """

    def __init__(self, bot):
        self.bot = bot
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._worker: asyncio.Task | None = None

    async def cog_load(self):
        self._worker = asyncio.create_task(self._drain(), name="channel-activity-writer")

    async def cog_unload(self):
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only track real human messages in guild channels.
        if message.author.bot or message.guild is None:
            return
        author_name = getattr(message.author, "display_name", None) or message.author.name
        author_name = author_name[:255]

        # Publish the live event immediately — it's an in-memory, non-blocking
        # put_nowait, so the dashboard WebSocket stays real-time regardless of DB
        # latency.
        events.publish({
            "type": "activity",
            "channel_id": str(message.channel.id),
            "guild_id": message.guild.id,
            "at": message.created_at.replace(tzinfo=datetime.timezone.utc).isoformat(),
            "author": author_name,
        })

        record = {
            "channel_id": message.channel.id,
            "guild_id": message.guild.id,
            "at": message.created_at.replace(tzinfo=None),  # store naive UTC, as elsewhere
            "author_id": message.author.id,
            "author_name": author_name,
        }
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            # Make room by discarding the oldest pending write; we only ever keep
            # the latest message per channel, so a dropped intermediate is fine.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                logger.warning("Channel activity queue full; dropping a record")

    async def _drain(self):
        """Single background consumer: serialise the blocking DB writes off-loop."""
        while True:
            record = await self._queue.get()
            try:
                await asyncio.to_thread(ChannelActivityDb.record, **record)
            except Exception:
                # Activity tracking must never break the bot.
                logger.exception("Failed to record channel activity")
            finally:
                self._queue.task_done()


async def setup(bot):
    await bot.add_cog(ChannelActivity(bot))

import asyncio
import datetime
import logging

import discord
from discord.ext import commands

from bot.store import ChannelActivityDb, PuzzleActivityDb
from bot.web import events

logger = logging.getLogger(__name__)

# Cap the backlog so a wedged/slow DB can't grow the queue without bound. At ~one
# row per human message this is generous; if we ever hit it we drop the oldest
# pending writes. channel_activity only keeps the *latest* message per channel, so
# that loses nothing there; the activity slots lose one chat count per dropped
# record, which is acceptable at this cap.
_QUEUE_MAXSIZE = 10000


class ChannelActivity(commands.Cog):
    """Records the most recent non-bot message per channel for the dashboard, and
    counts each one into its puzzle's 5-minute activity slot (the sparkline).

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
            # Make room by discarding the oldest pending write (see _QUEUE_MAXSIZE).
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
                # Two independent writes: one failing must not lose the other.
                # Activity tracking must never break the bot.
                try:
                    await asyncio.to_thread(ChannelActivityDb.record, **record)
                except Exception:
                    logger.exception("Failed to record channel activity")
                try:
                    await asyncio.to_thread(
                        PuzzleActivityDb.add_chat, record["channel_id"], record["at"])
                except Exception:
                    logger.exception("Failed to count chat into the activity slot")
            finally:
                self._queue.task_done()


async def setup(bot):
    await bot.add_cog(ChannelActivity(bot))

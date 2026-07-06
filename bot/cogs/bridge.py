import asyncio
import datetime
import logging

import aiohttp
import discord
from discord.ext import commands

from bot.store import MessageDb
from bot.core.message_media import media_root, attachment_relpath, download_attachment
from bot.utils import config

logger = logging.getLogger(__name__)

KIND_HUMAN = 0
KIND_THIS_BOT = 1
KIND_OTHER_BOT = 2
KIND_COMMAND = 3

_QUEUE_MAXSIZE = 10000
_REFRESH_SECONDS = 60


class MessageBridge(commands.Cog):
    """Forward-only mirror of hunt puzzle-channel messages into the web DB.

    Capture is inbound-only (gateway push events), so it makes no REST calls and
    consumes no rate-limit buckets. All DB writes and attachment downloads run on
    a background worker, off the gateway path (mirrors bot/cogs/activity.py).
    """

    def __init__(self, bot):
        self.bot = bot
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._worker: asyncio.Task | None = None
        self._refresher: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._puzzle_channels: set[int] = set()

    async def cog_load(self):
        if not config.web_bridge_enabled:
            logger.info("Message bridge disabled (web.bridge_enabled=false)")
            return
        self._puzzle_channels = await asyncio.to_thread(self._load_puzzle_channels)
        self._session = aiohttp.ClientSession()
        self._worker = asyncio.create_task(self._drain(), name="message-bridge-writer")
        self._refresher = asyncio.create_task(self._refresh_loop(), name="message-bridge-refresh")

    async def cog_unload(self):
        for task in (self._worker, self._refresher):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._worker = self._refresher = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ---- gating -----------------------------------------------------------
    @staticmethod
    def _load_puzzle_channels() -> set[int]:
        from bot.store import mydb
        cur = mydb.dict_cursor()
        try:
            cur.execute("SELECT channel_id FROM puzzles WHERE channel_id IS NOT NULL")
            rows = cur.fetchall() or []
        finally:
            cur.close()
        return {int(r["channel_id"]) for r in rows}

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(_REFRESH_SECONDS)
            try:
                self._puzzle_channels = await asyncio.to_thread(self._load_puzzle_channels)
            except Exception:
                logger.exception("Failed to refresh bridge puzzle-channel set")

    def _kind_for(self, message: discord.Message) -> int:
        if message.author.id == self.bot.application_id:
            return KIND_THIS_BOT
        if message.author.bot:
            return KIND_OTHER_BOT
        if message.content.startswith("!"):
            return KIND_COMMAND
        return KIND_HUMAN

    # ---- listeners --------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.channel.id not in self._puzzle_channels:
            return
        author_name = (getattr(message.author, "display_name", None) or message.author.name)[:255]
        avatar = getattr(getattr(message.author, "display_avatar", None), "url", None)
        rec = {
            "op": "insert",
            "id": message.id,
            "channel_id": message.channel.id,
            "guild_id": message.guild.id,
            "author_id": message.author.id,
            "author_name": author_name,
            "author_avatar": avatar,
            "kind": self._kind_for(message),
            "content": message.content or "",
            "reference_id": (message.reference.message_id if message.reference else None),
            "created_at": message.created_at.replace(tzinfo=None),
            "attachments": [
                {"id": a.id, "filename": a.filename, "content_type": a.content_type,
                 "size": a.size, "url": a.url}
                for a in message.attachments
            ],
        }
        self._enqueue(rec)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        content = (payload.data or {}).get("content")
        if content is None:
            return  # embed-only / non-content edit
        edited = (payload.data or {}).get("edited_timestamp")
        self._enqueue({"op": "edit", "id": payload.message_id,
                       "content": content, "edited_at": _parse_iso(edited)})

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        # No gate: if the id isn't ours the delete is a harmless no-op; if it is,
        # privacy requires purging it regardless of channel state.
        self._enqueue({"op": "delete", "id": payload.message_id})

    def _enqueue(self, rec: dict):
        try:
            self._queue.put_nowait(rec)
        except asyncio.QueueFull:
            logger.warning("Message bridge queue full; dropping a record")

    # ---- worker -----------------------------------------------------------
    async def _drain(self):
        while True:
            rec = await self._queue.get()
            try:
                await self._handle(rec)
            except Exception:
                logger.exception("Bridge worker failed on %r", rec.get("op"))
            finally:
                self._queue.task_done()

    async def _handle(self, rec: dict):
        op = rec["op"]
        if op == "insert":
            await asyncio.to_thread(
                MessageDb.write,
                id=rec["id"], channel_id=rec["channel_id"], guild_id=rec["guild_id"],
                author_id=rec["author_id"], author_name=rec["author_name"],
                author_avatar=rec["author_avatar"], kind=rec["kind"],
                content=rec["content"], reference_id=rec["reference_id"],
                created_at=rec["created_at"],
            )
            for att in rec["attachments"]:
                await self._store_attachment(rec, att)
        elif op == "edit":
            await asyncio.to_thread(MessageDb.mark_edited, rec["id"], rec["content"], rec["edited_at"])
        elif op == "delete":
            paths = await asyncio.to_thread(MessageDb.delete_message, rec["id"])
            for rel in paths:
                try:
                    (media_root() / rel).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Failed to unlink attachment bytes %s", rel)

    async def _store_attachment(self, rec: dict, att: dict):
        await asyncio.to_thread(
            MessageDb.add_attachment,
            id=att["id"], message_id=rec["id"], filename=att["filename"],
            content_type=att["content_type"], size=att["size"], source_url=att["url"],
        )
        rel = attachment_relpath(rec["guild_id"], rec["channel_id"], att["id"], att["filename"])
        dest = media_root() / rel
        assert self._session is not None
        ok = await download_attachment(self._session, att["url"], dest, config.web_attachment_max_bytes)
        await asyncio.to_thread(MessageDb.set_attachment_local, att["id"], rel if ok else None, 1 if ok else 2)


def _parse_iso(value):
    if not value:
        return datetime.datetime.utcnow().replace(microsecond=0)
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return datetime.datetime.utcnow().replace(microsecond=0)


async def setup(bot):
    await bot.add_cog(MessageBridge(bot))

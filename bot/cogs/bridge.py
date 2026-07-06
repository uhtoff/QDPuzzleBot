import asyncio
import datetime
import json
import logging

import aiohttp
import discord
from discord.ext import commands

from bot.store import MessageDb
from bot.core.message_media import media_root, attachment_relpath, download_attachment
from bot.utils import config
from bot.web import events

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
        self._non_puzzle_channels: set[int] = set()  # negative cache for the lazy gate

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
                self._non_puzzle_channels.clear()  # drop stale negatives so conversions re-check
            except Exception:
                logger.exception("Failed to refresh bridge puzzle-channel set")

    @staticmethod
    def _lookup_puzzle_channel(channel_id: int) -> bool:
        from bot.store import mydb
        cur = mydb.dict_cursor()
        try:
            cur.execute("SELECT 1 FROM puzzles WHERE channel_id=%s LIMIT 1", (channel_id,))
            return cur.fetchone() is not None
        finally:
            cur.close()

    async def _is_puzzle_channel(self, channel_id: int) -> bool:
        """Lazy gate: new puzzle channels are captured on their first message
        instead of waiting up to _REFRESH_SECONDS for the periodic reload. The
        positive set is checked first, so a conversion picked up by the refresh
        always wins over a stale negative-cache entry."""
        if channel_id in self._puzzle_channels:
            return True
        if channel_id in self._non_puzzle_channels:
            return False
        is_puzzle = await asyncio.to_thread(self._lookup_puzzle_channel, channel_id)
        (self._puzzle_channels if is_puzzle else self._non_puzzle_channels).add(channel_id)
        return is_puzzle

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
        if self._worker is None:
            return  # bridge disabled / not started — stay inert
        if message.guild is None:
            return
        if not await self._is_puzzle_channel(message.channel.id):
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
            "embeds": _embeds_json([e.to_dict() for e in message.embeds]),
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
        if self._worker is None:
            return  # bridge disabled / not started — stay inert
        content = (payload.data or {}).get("content")
        if content is None:
            return  # embed-only / non-content edit
        edited = (payload.data or {}).get("edited_timestamp")
        data = payload.data or {}
        rec = {"op": "edit", "id": payload.message_id,
               "channel_id": payload.channel_id, "guild_id": payload.guild_id,
               "content": content, "edited_at": _parse_iso(edited)}
        if "embeds" in data:  # only rewrite embeds when the payload actually carries them
            rec["embeds"] = _embeds_json(data["embeds"])
        self._enqueue(rec)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if self._worker is None:
            return  # bridge disabled / not started — stay inert
        # No channel gate: if the id isn't ours the delete is a harmless no-op; if
        # it is, privacy requires purging it regardless of channel state.
        self._enqueue({"op": "delete", "id": payload.message_id,
                       "channel_id": payload.channel_id, "guild_id": payload.guild_id})

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

    @staticmethod
    def _message_payload(rec: dict) -> dict:
        return {
            "id": str(rec["id"]),
            "author_id": str(rec["author_id"]),
            "author_name": rec["author_name"],
            "author_avatar": rec["author_avatar"],
            "kind": rec["kind"],
            "content": rec["content"],
            "reference_id": (str(rec["reference_id"]) if rec.get("reference_id") else None),
            "created_at": rec["created_at"].isoformat() if hasattr(rec["created_at"], "isoformat") else str(rec["created_at"]),
            "edited_at": None,
            "embeds": json.loads(rec["embeds"]) if rec.get("embeds") else [],
            "attachments": [],  # attachments arrive via a board refresh / reopen; live text is the priority
        }

    def _publish(self, event: dict):
        try:
            events.publish(event)
        except Exception:
            logger.exception("Bridge failed to publish %s", event.get("type"))

    async def _handle(self, rec: dict):
        op = rec["op"]
        if op == "insert":
            await asyncio.to_thread(
                MessageDb.write,
                id=rec["id"], channel_id=rec["channel_id"], guild_id=rec["guild_id"],
                author_id=rec["author_id"], author_name=rec["author_name"],
                author_avatar=rec["author_avatar"], kind=rec["kind"],
                content=rec["content"], reference_id=rec["reference_id"],
                created_at=rec["created_at"], embeds=rec.get("embeds"),
            )
            for att in rec["attachments"]:
                await self._store_attachment(rec, att)
            self._publish({"type": "message", "channel_id": str(rec["channel_id"]),
                           "guild_id": rec["guild_id"], "message": self._message_payload(rec)})
        elif op == "edit":
            if "embeds" in rec:
                await asyncio.to_thread(MessageDb.mark_edited, rec["id"], rec["content"],
                                        rec["edited_at"], rec["embeds"])
            else:
                await asyncio.to_thread(MessageDb.mark_edited, rec["id"], rec["content"], rec["edited_at"])
            msg = {"id": str(rec["id"]), "content": rec["content"]}
            if "embeds" in rec:
                msg["embeds"] = json.loads(rec["embeds"]) if rec["embeds"] else []
            self._publish({"type": "message_edit",
                           "channel_id": str(rec["channel_id"]) if rec.get("channel_id") else None,
                           "guild_id": rec.get("guild_id"),
                           "message": msg})
        elif op == "delete":
            paths = await asyncio.to_thread(MessageDb.delete_message, rec["id"])
            for rel in paths:
                try:
                    (media_root() / rel).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Failed to unlink attachment bytes %s", rel)
            self._publish({"type": "message_delete",
                           "channel_id": str(rec.get("channel_id")) if rec.get("channel_id") else None,
                           "guild_id": rec.get("guild_id"), "message_id": str(rec["id"])})

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


def _normalize_embeds(raw_embeds) -> list[dict]:
    """Reduce Discord embed dicts to the stable subset the viewer renders.

    Accepts the output of ``discord.Embed.to_dict()`` (live send path) or the raw
    embed dicts from a gateway MESSAGE_UPDATE payload — both share the Discord
    embed schema. Returns a list of ``{title, description, url, color, fields,
    footer}`` dicts (missing keys omitted)."""
    out: list[dict] = []
    for e in raw_embeds or []:
        if not isinstance(e, dict):
            continue
        norm: dict = {}
        for key in ("title", "description", "url", "color"):
            if e.get(key) is not None:
                norm[key] = e[key]
        fields = [
            {"name": f.get("name", ""), "value": f.get("value", ""),
             "inline": bool(f.get("inline", False))}
            for f in (e.get("fields") or []) if isinstance(f, dict)
        ]
        if fields:
            norm["fields"] = fields
        footer = e.get("footer")
        if isinstance(footer, dict) and footer.get("text"):
            norm["footer"] = footer["text"]
        if norm:
            out.append(norm)
    return out


def _embeds_json(raw_embeds) -> str | None:
    """Normalized embeds as a JSON string for storage, or None when empty."""
    norm = _normalize_embeds(raw_embeds)
    return json.dumps(norm) if norm else None


def _parse_iso(value):
    if not value:
        return datetime.datetime.utcnow().replace(microsecond=0)
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return datetime.datetime.utcnow().replace(microsecond=0)


async def setup(bot):
    await bot.add_cog(MessageBridge(bot))

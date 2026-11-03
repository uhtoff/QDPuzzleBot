"""Hardened Discord channel-archiving primitives (ctx-free).

Copies a channel's human message history into a thread on a destination channel
via a webhook (impersonating original authors), skipping bot/command/empty
messages, then optionally deletes the source. Built to run unattended on large,
attachment-heavy, odd-emoji channels without hanging:

  * attachment downloads are bounded by a timeout and never abort the run
  * reactions are NOT copied (foreign/custom emoji can't be re-applied and cost
    an API round-trip each)
  * sends use allowed_mentions=none + silent so nothing pings

Used by the cog archive commands and by PuzzleService.archive_hunt.
"""
import asyncio
import logging
from dataclasses import dataclass, field

import discord

from bot.store import GuildSettingsDb, PuzzleJsonDb
from bot.core import role_gate
from bot.core.constants import ADMIN_ROLES
from bot.core.discord_channels import DELETE_REASON

logger = logging.getLogger(__name__)

# Bound each attachment re-download so one stuck CDN fetch can't hang the archive.
ATTACH_TIMEOUT = 30
# Small politeness gap between webhook sends, on top of discord.py's own 429 handling.
SEND_DELAY = 0.1
# Discord thread-name hard cap.
MAX_THREAD_NAME = 100
# Appended to an archive thread's starter message once its whole history was
# copied; a thread without it was left by a failed or interrupted copy.
ARCHIVE_COMPLETE_MARK = " — complete"


@dataclass
class ChannelArchiveResult:
    thread: object = None
    messages_moved: int = 0
    messages_skipped: int = 0
    attachment_failures: int = 0


@dataclass
class HuntArchiveReport:
    channels_archived: int = 0
    voice_deleted: int = 0
    messages_moved: int = 0
    channels_skipped: int = 0
    categories_deleted: int = 0
    failures: list = field(default_factory=list)
    final_name: str = ""
    # archive_hunt_to_hub only: the hub channel the history landed in.
    hub_channel_id: int = 0
    # archive_hunt_to_hub only: True once the bot has left the emptied team server.
    left_guild: bool = False


def resolve_team_name(guild, hunt, invoker) -> str:
    """The hunt's gating-role name (hunt.role_id); the invoker's display name if the
    hunt has no role (role_id 0) or the role can no longer be resolved."""
    role = guild.get_role(hunt.role_id) if getattr(hunt, "role_id", 0) else None
    if role is not None:
        return role.name
    return invoker.display_name


def archived_channel_name(hunt, team) -> str:
    """'<YYYY-MM> <hunt.name> - <team>' when the hunt has a start_time, else
    '<hunt.name> - <team>'."""
    start = getattr(hunt, "start_time", None)
    if start is not None:
        return f"{start:%Y-%m} {hunt.name} - {team}"
    return f"{hunt.name} - {team}"


class HubUnavailable(Exception):
    """The named hub can't take this archive. Raised before anything is
    touched; the message is shown to the invoker as-is."""


def choose_hub_id(given_hub_id, roster_hub_id):
    """The hub server !archive_hunt_to_hub archives to. *roster_hub_id* is the
    server holding the hunt's hub role (None if it has none): role ids are
    unique, so that server is the hub. An explicit *given_hub_id* is for hunts
    without a hub role; one that disagrees with the hub role is refused."""
    if given_hub_id is None:
        if roster_hub_id is None:
            raise HubUnavailable("this hunt has no hub role to find its hub from — give the "
                                 "hub's server id: `!archive_hunt_to_hub <hub server id>`")
        return roster_hub_id
    if roster_hub_id is not None and given_hub_id != roster_hub_id:
        raise HubUnavailable(f"this hunt's hub role is on server {roster_hub_id}, not "
                             f"{given_hub_id} — leave the id out to archive there")
    return given_hub_id


async def hub_member(hub, user_id):
    """The invoker's member object on the hub, or None. Once the hub is chunked
    the cache is complete, so a miss is authoritative."""
    member = hub.get_member(user_id)
    if member is None and not hub.chunked:
        try:
            member = await hub.fetch_member(user_id)
        except discord.NotFound:
            member = None
    return member


async def resolve_hub(bot, hub_guild_id, guild, invoker):
    """(hub guild, hub `guilds` row) for !archive_hunt_to_hub, or HubUnavailable.

    The invoker must hold the Admin tier ON THE HUB, so nobody can dump a hunt
    into a hub they don't run. The row is needed to re-home the hunt:
    hunts.guild_id is an FK to guilds.id (the row id), not the snowflake."""
    if hub_guild_id == guild.id:
        raise HubUnavailable("that's this server — use !archive_hunt to archive in place")
    hub = bot.get_guild(hub_guild_id)
    if hub is None:
        raise HubUnavailable(f"the bot isn't in server {hub_guild_id}")
    perms = hub.me.guild_permissions
    if not (perms.manage_channels and perms.manage_webhooks):
        raise HubUnavailable(f"the bot needs Manage Channels and Manage Webhooks on {hub.name}")
    member = await hub_member(hub, invoker.id)
    if member is None:
        raise HubUnavailable(f"you aren't a member of {hub.name}")
    if not role_gate.member_holds_tier({r.id for r in member.roles}, hub, ADMIN_ROLES):
        raise HubUnavailable(f"you aren't an admin on {hub.name}")
    row = GuildSettingsDb.get(hub.id)
    if row is None:
        raise HubUnavailable(f"{hub.name} has no guilds row (re-invite the bot there)")
    return hub, row


def archive_topic(hunt) -> str:
    """Topic tagging a hub archive channel with its hunt, so a re-run finds it."""
    return f"puzzbot-archive:{hunt.uid}"


async def ensure_hub_archive_channel(hub, hunt, name):
    """The hub channel this hunt archives into: the one already tagged for it
    (a prior partial run), else a new uncategorised one named *name*, the same
    place archive_hunt leaves its surviving channel today."""
    return await ensure_tagged_channel(hub, archive_topic(hunt), name)


async def ensure_tagged_channel(hub, marker, name):
    """The hub text channel whose topic is *marker*, else a new uncategorised one
    named *name*."""
    for ch in hub.text_channels:
        if ch.topic == marker:
            return ch
    return await hub.create_text_channel(name[:100], topic=marker)  # Discord's cap


def thread_name_for_channel(channel) -> str:
    """Name the archive thread after the puzzle (if this is a puzzle channel), else
    the channel; suffixed with the category name and capped at MAX_THREAD_NAME."""
    puzzle = PuzzleJsonDb.get_by_attr(channel_id=channel.id)
    category = getattr(channel, "category", None)
    round_name = category.name if category is not None else ""
    base = puzzle.name if puzzle else channel.name
    return f"{base} ({round_name})"[:MAX_THREAD_NAME]


async def ensure_webhook(channel) -> discord.Webhook:
    """Return an existing webhook on *channel*, creating one if there is none."""
    hooks = await channel.webhooks()
    if hooks:
        return hooks[0]
    return await channel.create_webhook(name="Archiver")


async def _gather_messages(source):
    """Every message in *source* and its active + archived threads, oldest first."""
    messages = []
    async for m in source.history(limit=None, oldest_first=True):
        messages.append(m)
    for thread in source.threads:
        async for m in thread.history(limit=None, oldest_first=True):
            messages.append(m)
    async for archived in source.archived_threads():
        async for m in archived.history(limit=None, oldest_first=True):
            messages.append(m)
    return sorted(messages, key=lambda m: m.created_at)


async def archive_channel_to_thread(bot, dest, webhook, source, *, thread_name) -> ChannelArchiveResult:
    """Copy *source*'s human history into a new thread on *dest*. Archive only,
    no delete. Skips the bot's own messages, `!command` messages, and empty
    messages; drops reactions; never pings; never hangs on a stuck download."""
    result = ChannelArchiveResult()
    placeholder = await dest.send(content=f"Archive of channel {source.name}", silent=True)
    thread = await dest.create_thread(name=thread_name, message=placeholder)
    result.thread = thread
    bot_id = bot.application_id
    for message in await _gather_messages(source):
        if not message:
            continue
        author = message.author
        if author.id == bot_id:
            continue
        content = message.content
        if content and content[0] == "!" and content[:3] != "!s ":
            continue
        files = []
        for a in message.attachments:
            try:
                files.append(await asyncio.wait_for(a.to_file(use_cached=True), timeout=ATTACH_TIMEOUT))
            except Exception as e:  # timeout / aiohttp / HTTP — skip the file, keep the message
                result.attachment_failures += 1
                logger.warning("Skipping attachment %r while archiving: %s", getattr(a, "filename", "?"), e)
        if not content and not files and not message.embeds:
            result.messages_skipped += 1
            continue
        avatar_url = getattr(author.avatar, "url", None)
        try:
            await webhook.send(
                content=content,
                username=author.display_name,
                avatar_url=avatar_url,
                files=files,
                embeds=message.embeds,
                thread=thread,
                wait=True,
                silent=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            result.messages_moved += 1
        except discord.HTTPException as e:
            result.messages_skipped += 1
            logger.warning("Skipping message from %s while archiving: %s", author.display_name, e)
        await asyncio.sleep(SEND_DELAY)
    # Only reached when the whole history was copied: marks the thread done.
    await placeholder.edit(content=f"Archive of channel {source.name}{ARCHIVE_COMPLETE_MARK}")
    return result


async def archive_thread_state(dest, thread_name):
    """(thread, complete) for an active archive thread named *thread_name* on
    *dest*, or (None, False) if there is none. Complete means its starter
    message carries ARCHIVE_COMPLETE_MARK, which archive_channel_to_thread only
    writes after the whole history was copied, so a thread left by a failed or
    interrupted copy reads as incomplete.

    Threads written before the mark existed carry none, so a resumed run
    spanning that deploy re-copies them instead of skipping them: history is
    duplicated rather than lost."""
    thread = next((t for t in getattr(dest, "threads", [])
                   if getattr(t, "name", None) == thread_name), None)
    if thread is None:
        return None, False
    try:
        starter = await dest.fetch_message(thread.id)
    except discord.NotFound:
        return thread, False
    return thread, (starter.content or "").endswith(ARCHIVE_COMPLETE_MARK)


async def discard_partial_thread(dest, thread):
    """Delete an incomplete archive thread and its starter message, so the
    channel can be copied again from scratch."""
    await thread.delete()
    try:
        await dest.get_partial_message(thread.id).delete()
    except discord.HTTPException:
        pass


async def archive_and_delete_channel(bot, dest, webhook, source, *, thread_name) -> ChannelArchiveResult:
    """archive_channel_to_thread, then delete *source* on success."""
    result = await archive_channel_to_thread(bot, dest, webhook, source, thread_name=thread_name)
    await source.delete(reason=DELETE_REASON)
    return result

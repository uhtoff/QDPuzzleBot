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

from bot.store import PuzzleJsonDb
from bot.core.discord_channels import DELETE_REASON

logger = logging.getLogger(__name__)

# Bound each attachment re-download so one stuck CDN fetch can't hang the archive.
ATTACH_TIMEOUT = 30
# Small politeness gap between webhook sends, on top of discord.py's own 429 handling.
SEND_DELAY = 0.1
# Discord thread-name hard cap.
MAX_THREAD_NAME = 100


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

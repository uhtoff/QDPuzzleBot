"""Puzzle and hunt voice channels: creation (Task 2) and warned close (Task 3).

Voice work is always best-effort for callers; this module raises ``VoiceError``
only for refusals a user should be told about.
"""
import asyncio
import datetime
import logging

import discord

from bot.core.discord_channels import MAX_CATEGORY_CHANNELS
from bot.store import GuildSettingsDb, HuntJsonDb, PuzzleJsonDb

logger = logging.getLogger(__name__)


class VoiceError(Exception):
    """A refusal; ``code`` is one of no_hunt_category, solved, archived, not_a_puzzle."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def voice_category_name(hunt_name: str, n: int) -> str:
    suffix = " Voice" if n == 1 else f" Voice {n}"
    return hunt_name[:100 - len(suffix)] + suffix


def voice_url(guild_snowflake: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{guild_snowflake}/{channel_id}"


# One lock per hunt serialises check-then-create so concurrent callers can't
# duplicate the hunt voice channel, a voice category, or a puzzle's channel.
_hunt_locks: dict[int, asyncio.Lock] = {}


def _hunt_lock(hunt) -> asyncio.Lock:
    return _hunt_locks.setdefault(hunt.id, asyncio.Lock())


def _has_room(category) -> bool:
    return len(category.channels) < MAX_CATEGORY_CHANNELS


async def ensure_hunt_voice(guild, hunt) -> discord.VoiceChannel | None:
    async with _hunt_lock(hunt):
        return await _ensure_hunt_voice_locked(guild, hunt)


async def _ensure_hunt_voice_locked(guild, hunt) -> discord.VoiceChannel | None:
    """The hunt's voice channel, created in its main category if missing.
    None (logged) when the main category is missing or full."""
    if hunt.voice_channel_id:
        existing = guild.get_channel(hunt.voice_channel_id)
        if existing is not None:
            return existing
    category = guild.get_channel(hunt.category_id)
    if category is None or not _has_room(category):
        logger.warning("No room for a voice channel for hunt %s (category %s missing or full)",
                       hunt.id, hunt.category_id)
        return None
    channel = await guild.create_voice_channel(f"{hunt.name} voice"[:100], category=category)
    HuntJsonDb.set_voice_channel(hunt.id, channel.id)
    hunt.voice_channel_id = channel.id
    return channel


async def ensure_voice_category(guild, hunt) -> discord.CategoryChannel:
    async with _hunt_lock(hunt):
        return await _ensure_voice_category_locked(guild, hunt)


async def _ensure_voice_category_locked(guild, hunt) -> discord.CategoryChannel:
    """The first "<hunt> Voice[ n]" category with room, cloned from the hunt's
    main category (so it carries the hunt's permissions) when none exists."""
    hunt_category = guild.get_channel(hunt.category_id)
    if hunt_category is None:
        raise VoiceError("no_hunt_category",
                         "I can't find the hunt's main category to copy permissions from")
    by_name = {c.name: c for c in guild.categories}
    n = 1
    while True:
        found = by_name.get(voice_category_name(hunt.name, n))
        if found is None:
            break
        if _has_room(found):
            return found
        n += 1
    return await hunt_category.clone(name=voice_category_name(hunt.name, n))


async def create_puzzle_voice(guild, hunt, puzzle) -> tuple[discord.VoiceChannel, bool]:
    """(channel, created) for the puzzle's voice channel, creating it if needed."""
    if hunt.archived:
        raise VoiceError("archived")
    if puzzle.solved:
        raise VoiceError("solved")
    async with _hunt_lock(hunt):
        if puzzle.voice_channel_id:
            existing = guild.get_channel(puzzle.voice_channel_id)
            if existing is not None:
                return existing, False
        await _ensure_hunt_voice_locked(guild, hunt)
        category = await _ensure_voice_category_locked(guild, hunt)
        channel = await guild.create_voice_channel(puzzle.name[:100], category=category)
        PuzzleJsonDb.set_voice_channel(puzzle.id, channel.id)
        puzzle.voice_channel_id = channel.id
        return channel, True


# ---- close, cleanup, solved sweep ------------------------------------------

WARNING_SECONDS = 15
SOLVED_GRACE = datetime.timedelta(minutes=15)
CLEANUP_REASON = "an admin is tidying up voice channels"

# Ids of channels currently inside close_voice_channel, so the 20 s sweep firing
# again during a 15 s close neither re-warns nor double-deletes.
_closing: set[int] = set()


async def close_voice_channel(guild, hunt, channel, reason: str, *, sleep=asyncio.sleep) -> bool:
    """Warn (if occupied), move occupants to the hunt voice channel, delete.
    Does not touch the database. True if it deleted the channel, False if it
    skipped because a close is already in flight."""
    if channel.id in _closing:
        return False
    _closing.add(channel.id)
    try:
        if channel.members:
            target = await ensure_hunt_voice(guild, hunt)
            mention = target.mention if target is not None else "the hunt's voice channel"
            await channel.send(
                f"@here this voice channel closes in {WARNING_SECONDS} seconds — {reason}. "
                f"Anyone still here will be moved to {mention}.",
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
            await sleep(WARNING_SECONDS)
            if target is not None:
                for member in list(channel.members):
                    try:
                        await member.move_to(target)
                    except Exception:
                        logger.warning("Could not move %s out of voice channel %s",
                                       getattr(member, "name", member), channel.id, exc_info=True)
        try:
            await channel.delete(reason=reason)
        except discord.NotFound:
            pass
        return True
    finally:
        _closing.discard(channel.id)


async def close_puzzle_voice(guild, hunt, puzzle, reason: str, *, sleep=asyncio.sleep) -> bool:
    """Close the puzzle's voice channel and forget its id. True if a channel was deleted."""
    channel = guild.get_channel(puzzle.voice_channel_id) if puzzle.voice_channel_id else None
    if channel is None:
        PuzzleJsonDb.set_voice_channel(puzzle.id, None)
        puzzle.voice_channel_id = 0
        return False
    if not await close_voice_channel(guild, hunt, channel, reason, sleep=sleep):
        return False
    PuzzleJsonDb.set_voice_channel(puzzle.id, None)
    puzzle.voice_channel_id = 0
    return True


def is_voice_category_name(hunt_name: str, name: str) -> bool:
    """True if *name* is one of the hunt's "<hunt> Voice[ N]" category names."""
    if not isinstance(hunt_name, str) or not isinstance(name, str):
        return False
    base = voice_category_name(hunt_name, 1)
    if name == base:
        return True
    rest = name[len(base) + 1:] if name.startswith(base + " ") else ""
    return rest.isdigit()


def hunt_voice_categories(guild, hunt) -> list:
    """The hunt's "<hunt> Voice[ N]" categories. A category holding anything but
    voice channels is not one of them, even if the name matches (e.g. the main
    category of a hunt that is itself called "<hunt> Voice")."""
    return [c for c in guild.categories
            if is_voice_category_name(hunt.name, c.name)
            and all(isinstance(ch, discord.VoiceChannel) for ch in c.channels)]


async def cleanup_hunt(guild, hunt, *, sleep=asyncio.sleep) -> list[str]:
    """Remove every puzzle voice channel of the hunt (plus orphans left in its
    voice categories) and then its empty voice categories. Never touches the
    hunt voice channel. Returns the names of removed channels."""
    puzzles = await asyncio.to_thread(PuzzleJsonDb.with_voice, hunt.id)
    stored = {p.voice_channel_id for p in puzzles}
    keep = stored | ({hunt.voice_channel_id} if hunt.voice_channel_id else set())
    categories = [c for c in guild.categories if is_voice_category_name(hunt.name, c.name)]

    names: list[str] = []
    removed_ids: set[int] = set()
    jobs = []

    async def _puzzle_job(puzzle):
        channel = guild.get_channel(puzzle.voice_channel_id)
        if await close_puzzle_voice(guild, hunt, puzzle, CLEANUP_REASON, sleep=sleep):
            names.append(channel.name)
            removed_ids.add(channel.id)

    async def _orphan_job(channel):
        if await close_voice_channel(guild, hunt, channel, CLEANUP_REASON, sleep=sleep):
            names.append(channel.name)
            removed_ids.add(channel.id)

    for puzzle in puzzles:
        jobs.append(_puzzle_job(puzzle))
    for category in categories:
        for channel in list(category.channels):
            if channel.id in keep or isinstance(channel, discord.TextChannel):
                continue
            jobs.append(_orphan_job(channel))
    for result in await asyncio.gather(*jobs, return_exceptions=True):
        if isinstance(result, BaseException):
            logger.error("Voice cleanup step failed", exc_info=result)

    for category in categories:
        if all(c.id in removed_ids for c in category.channels):
            try:
                await category.delete(reason=CLEANUP_REASON)
            except Exception:
                logger.warning("Could not delete voice category %s", category.id, exc_info=True)
    return names


def _guild_for_hunt(bot, hunt):
    """The discord.Guild homing *hunt* (hunts.guild_id is the guilds table row id)."""
    for guild in bot.guilds:
        settings = GuildSettingsDb.get_cached(guild.id)
        if settings is not None and settings.id == hunt.guild_id:
            return guild
    return None


async def sweep_solved(bot, *, now=None, sleep=asyncio.sleep) -> None:
    """Close the voice channel of every puzzle solved at least SOLVED_GRACE ago."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    now = now.replace(tzinfo=None)  # solve_time is stored as naive UTC
    hunts: dict = {}

    async def _close(guild, hunt, puzzle):
        try:
            await close_puzzle_voice(guild, hunt, puzzle, f"**{puzzle.name}** was solved",
                                     sleep=sleep)
        except Exception:
            logger.exception("Voice sweep failed for puzzle %s", puzzle.id)

    jobs = []
    for puzzle in await asyncio.to_thread(PuzzleJsonDb.voice_due, now - SOLVED_GRACE):
        try:
            if puzzle.hunt_id not in hunts:
                hunt = await asyncio.to_thread(HuntJsonDb.get_by_attr, id=puzzle.hunt_id)
                hunts[puzzle.hunt_id] = (hunt, _guild_for_hunt(bot, hunt) if hunt else None)
            hunt, guild = hunts[puzzle.hunt_id]
            if hunt is None or guild is None:
                continue
            jobs.append(_close(guild, hunt, puzzle))
        except Exception:
            logger.exception("Voice sweep could not resolve puzzle %s", puzzle.id)
    await asyncio.gather(*jobs)


async def delete_puzzle_voice_now(guild, puzzle) -> None:
    """Delete the puzzle's voice channel at once, no warning (the puzzle is going away)."""
    channel = guild.get_channel(puzzle.voice_channel_id) if puzzle.voice_channel_id else None
    if channel is None:
        return
    try:
        await channel.delete(reason=f"puzzle {puzzle.name} deleted")
    except discord.NotFound:
        pass

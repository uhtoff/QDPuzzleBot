"""Puzzle and hunt voice channels: creation (Task 2) and warned close (Task 3).

Voice work is always best-effort for callers; this module raises ``VoiceError``
only for refusals a user should be told about.
"""
import asyncio
import datetime
import logging
from collections.abc import Awaitable, Callable

import discord

from bot.core.discord_channels import MAX_CATEGORY_CHANNELS
from bot.store import GuildSettingsDb, HuntJsonDb, PuzzleJsonDb

logger = logging.getLogger(__name__)


class VoiceError(Exception):
    """A refusal; ``code`` is one of no_hunt_category, solved, archived, mini, not_a_puzzle."""

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


MOVE_MEMBERS_WARNING = ("I don't have **Move Members**, so people can't be moved out "
                        "when this channel closes — they'll be disconnected instead.")


def can_move_members(guild, hunt) -> bool:
    """Whether the bot may move people out of a closing voice channel: its
    permission on the hunt's main category (which the voice categories copy),
    or guild-wide when that category is gone."""
    me = guild.me
    category = _live(guild, hunt.category_id)
    perms = category.permissions_for(me) if category is not None else me.guild_permissions
    return bool(perms.move_members)


async def ensure_hunt_voice(guild, hunt) -> discord.VoiceChannel | None:
    async with _hunt_lock(hunt):
        return await _ensure_hunt_voice_locked(guild, hunt)


def _live(guild, channel_id):
    return guild.get_channel(channel_id) if channel_id else None


async def _ensure_hunt_voice_locked(guild, hunt) -> discord.VoiceChannel | None:
    """The hunt's voice channel, created in its main category if missing.
    None (logged) when the main category is missing or full."""
    existing = _live(guild, hunt.voice_channel_id)
    if existing is not None:
        return existing
    # *hunt* may be a stale object loaded before another caller made the
    # channel: re-read the stored id (under the lock) before creating one.
    hunt.voice_channel_id = await asyncio.to_thread(HuntJsonDb.voice_channel_id, hunt.id) or 0
    existing = _live(guild, hunt.voice_channel_id)
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
    if getattr(puzzle, "parent_puzzle_id", None):
        # Minis share their minimeta's voice channel (user, 2026-11-08).
        raise VoiceError("mini")
    async with _hunt_lock(hunt):
        existing = _live(guild, puzzle.voice_channel_id)
        if existing is None:
            # Stale-object guard, as in _ensure_hunt_voice_locked.
            puzzle.voice_channel_id = await asyncio.to_thread(
                PuzzleJsonDb.voice_channel_id, puzzle.id) or 0
            existing = _live(guild, puzzle.voice_channel_id)
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
SOLVED_REASON = "the puzzle was solved"

# Ids of channels currently inside close_voice_channel, so the 20 s sweep firing
# again during a 15 s close neither re-warns nor double-deletes.
_closing: set[int] = set()


StillWanted = Callable[[], Awaitable[bool]]


async def close_voice_channel(guild, hunt, channel, reason: str, *, sleep=asyncio.sleep,
                              still_wanted: StillWanted | None = None,
                              notice_channel=None, label: str | None = None) -> bool:
    """Warn (if occupied), move occupants to the hunt voice channel, delete.
    Does not touch the database. True if it deleted the channel, False if it
    skipped because a close is already in flight, or because *still_wanted*
    (checked after the warning) said the close is no longer wanted.

    The warning goes to *notice_channel* (a text channel people actually read —
    the puzzle's or the hunt's), mentioning only the people in the call; without
    one it falls back to the voice channel's own chat. A voice channel's chat is
    easy to miss and vanishes with the channel (user test, 2026-11-08)."""
    if channel.id in _closing:
        return False
    _closing.add(channel.id)
    try:
        if channel.members:
            target = await ensure_hunt_voice(guild, hunt)
            people = " ".join(m.mention for m in channel.members if not getattr(m, "bot", False))
            where = (f"You'll be moved to {target.mention}." if target is not None
                     else "You'll be disconnected when it closes.")
            text = (f"{people} the voice channel for **{label or channel.name}** closes in "
                    f"{WARNING_SECONDS} seconds — {reason}. {where}").strip()
            try:
                await (notice_channel or channel).send(
                    text,
                    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                                                             users=True),
                )
            except Exception:
                logger.warning("Voice close warning could not be posted for %s", channel.id,
                               exc_info=True)
            await sleep(WARNING_SECONDS)
            if still_wanted is not None and not await still_wanted():
                return False
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


def _notice_channel(guild, hunt, puzzle=None):
    """Where a close warning is posted: the puzzle's text channel, else the
    hunt's main text channel, else None (the voice channel's own chat)."""
    for cid in (getattr(puzzle, "channel_id", 0), getattr(hunt, "channel_id", 0)):
        ch = guild.get_channel(cid) if cid else None
        if ch is not None:
            return ch
    return None


async def close_puzzle_voice(guild, hunt, puzzle, reason: str, *, sleep=asyncio.sleep,
                             still_wanted: StillWanted | None = None) -> bool:
    """Close the puzzle's voice channel and forget its id. True if a channel was
    deleted; False (id kept) if none was, including when *still_wanted* aborts."""
    channel = guild.get_channel(puzzle.voice_channel_id) if puzzle.voice_channel_id else None
    if channel is None:
        PuzzleJsonDb.set_voice_channel(puzzle.id, None)
        puzzle.voice_channel_id = 0
        return False
    notice = _notice_channel(guild, hunt, puzzle)
    if not await close_voice_channel(guild, hunt, channel, reason, sleep=sleep,
                                     still_wanted=still_wanted, notice_channel=notice,
                                     label=puzzle.name):
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


async def cleanup_hunt(guild, hunt, *, bot=None, sleep=asyncio.sleep) -> list[str]:
    """Remove every puzzle voice channel of the hunt (plus orphans left in its
    voice categories) and then its empty voice categories. Never touches the
    hunt voice channel. Returns the names of removed channels. With *bot*, each
    affected puzzle's pinned info is refreshed (its voice link goes).

    Also turns the hunt's "voice channel for every new puzzle" setting off, first,
    so no new puzzle gets one mid-cleanup: removing them all and leaving the
    setting on would just start refilling (user, 2026-11-08)."""
    await asyncio.to_thread(HuntJsonDb.set_voice_per_puzzle, hunt.id, False)
    hunt.voice_per_puzzle = False
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
            if bot is not None:
                await refresh_pin(bot, guild, hunt, puzzle)

    async def _orphan_job(channel):
        if await close_voice_channel(guild, hunt, channel, CLEANUP_REASON, sleep=sleep,
                                     notice_channel=_notice_channel(guild, hunt)):
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


async def refresh_pin(bot, guild, hunt, puzzle=None) -> None:
    """Best-effort refresh of the puzzle's (or, with no puzzle, the hunt's)
    pinned info message, so its voice link matches the channel's existence."""
    try:
        cog = bot.get_cog("Puzzles")
        if cog is not None:
            await cog.service.notifier.refresh_info(guild, hunt, puzzle)
    except Exception:
        logger.exception("Voice pin refresh failed for hunt %s", getattr(hunt, "id", "?"))


async def notify_board(bot, hunt) -> None:
    """Best-effort dashboard board push for *hunt*, through the Puzzles
    service's notifier (the same sink every service mutation uses)."""
    try:
        cog = bot.get_cog("Puzzles")
        if cog is not None:
            await cog.service.notifier.board_changed(hunt)
    except Exception:
        logger.exception("Voice board push failed for hunt %s", getattr(hunt, "id", "?"))


async def sweep_solved(bot, *, now=None, sleep=asyncio.sleep) -> None:
    """Close the voice channel of every puzzle solved at least SOLVED_GRACE ago."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    now = now.replace(tzinfo=None)  # solve_time is stored as naive UTC
    hunts: dict = {}
    changed: dict = {}  # hunt id -> hunt, for hunts that lost a channel

    async def _close(guild, hunt, puzzle):
        async def still_solved() -> bool:
            # Unsolved during the warning: keep the channel (fresh read).
            return await asyncio.to_thread(PuzzleJsonDb.is_solved, puzzle.id)

        try:
            if await close_puzzle_voice(guild, hunt, puzzle, SOLVED_REASON,
                                        sleep=sleep, still_wanted=still_solved):
                changed[hunt.id] = hunt
                await refresh_pin(bot, guild, hunt, puzzle)
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
    for hunt in changed.values():
        await notify_board(bot, hunt)


async def rename_puzzle_voice(guild, puzzle, name: str) -> None:
    """Rename the puzzle's voice channel to follow the puzzle (no-op when it has
    none, it's gone, or the name already matches)."""
    channel = _live(guild, puzzle.voice_channel_id)
    new_name = name[:100]
    if channel is not None and channel.name != new_name:
        await channel.edit(name=new_name)


async def delete_puzzle_voice_now(guild, puzzle) -> None:
    """Delete the puzzle's voice channel at once, no warning (the puzzle is going away)."""
    channel = guild.get_channel(puzzle.voice_channel_id) if puzzle.voice_channel_id else None
    if channel is None:
        return
    try:
        await channel.delete(reason=f"puzzle {puzzle.name} deleted")
    except discord.NotFound:
        pass

"""Puzzle and hunt voice channels: creation (Task 2) and warned close (Task 3).

Voice work is always best-effort for callers; this module raises ``VoiceError``
only for refusals a user should be told about.
"""
import logging

from bot.core.discord_channels import MAX_CATEGORY_CHANNELS
from bot.store import HuntJsonDb, PuzzleJsonDb

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


def _has_room(category) -> bool:
    return len(category.channels) < MAX_CATEGORY_CHANNELS


async def ensure_hunt_voice(guild, hunt):
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


async def ensure_voice_category(guild, hunt):
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


async def create_puzzle_voice(guild, hunt, puzzle):
    """(channel, created) for the puzzle's voice channel, creating it if needed."""
    if hunt.archived:
        raise VoiceError("archived")
    if puzzle.solved:
        raise VoiceError("solved")
    if puzzle.voice_channel_id:
        existing = guild.get_channel(puzzle.voice_channel_id)
        if existing is not None:
            return existing, False
    await ensure_hunt_voice(guild, hunt)
    category = await ensure_voice_category(guild, hunt)
    channel = await guild.create_voice_channel(puzzle.name[:100], category=category)
    PuzzleJsonDb.set_voice_channel(puzzle.id, channel.id)
    puzzle.voice_channel_id = channel.id
    return channel, True

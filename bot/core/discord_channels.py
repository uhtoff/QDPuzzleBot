"""Ctx-free Discord channel/category adapter helpers (M3.5).

Extracted from bot.cogs.puzzles.Puzzles so the service layer can use them
without a cog or ctx dependency.
"""
import logging
import re
import string

import discord

from bot.utils import config


logger = logging.getLogger(__name__)

# ASCII punctuation Discord strips from text-channel names: everything in
# string.punctuation except '-' and '_', which Discord keeps. Non-ASCII
# characters (emoji, em/en dashes, £, accents, styled fonts) are left
# untouched, matching Discord's real normalisation.
_STRIP_PUNCT = re.compile(
    "[" + re.escape("".join(c for c in string.punctuation if c not in "-_")) + "]"
)

# --- Constants (re-exposed so callers don't reach into the cog) ---

SOLVE_CATEGORY = False
SOLVE_DIVIDER = "———solved———"
MAX_CHANNELS_PER_GUILD = 500
# Discord's real cap is 50; config.max_channels_per_category overrides it so a
# test config can exercise category split/overflow without creating 45+ channels.
MAX_CHANNELS_PER_CATEGORY = config.max_channels_per_category
MAX_CATEGORIES = 50
DELETE_REASON = "bot-delete"


# ------------------------------------------------------------------ helpers
def clean_channel_name(name):
    """Slugify *name* for a Discord channel, matching Discord's own
    text-channel normalisation.

    Lowercase, spaces to hyphens, strip ASCII punctuation except '-'/'_',
    collapse hyphen runs, trim, and cap at Discord's 100-char limit. Non-ASCII
    characters (emoji, em/en dashes, £, accents) pass through unchanged, so the
    stored name matches the channel Discord actually creates.
    """
    name = _STRIP_PUNCT.sub("", name.strip().lower())
    name = re.sub(r"\s+", "-", name)   # spaces -> hyphens
    name = re.sub(r"-+", "-", name)    # collapse hyphen runs
    return name.strip("-")[:100].rstrip("-")


def clean_category_name(name):
    """Lightly clean *name* for a Discord category, which Discord stores
    verbatim (only force-uppercased for display). Collapse internal whitespace
    to single spaces, trim, and cap at Discord's 100-char limit; case,
    punctuation and emoji are preserved.
    """
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100].strip()


def solve_divider_position(category):
    """Return the position of the SOLVE_DIVIDER channel in *category*, or False."""
    for channel in category.channels:
        if channel.name == SOLVE_DIVIDER:
            return channel.position
    return False


def first_channel_position(category):
    """Return the lowest channel position in *category*, or False."""
    position = False
    for channel in category.channels:
        if position is False or channel.position < position:
            position = channel.position
    return position


async def get_or_create_channel(
    guild,
    category: "discord.CategoryChannel | None",
    channel_name: str,
    channel_type,
    **kwargs,
):
    """Retrieve given channel by name/category or create one.

    Returns (channel, created) tuple — mirrors the old cog method exactly.
    """
    if channel_type == "text":
        channel_type = discord.ChannelType.text
    elif channel_type == "voice":
        channel_type = discord.ChannelType.voice
    if not (channel_type is discord.ChannelType.text or channel_type is discord.ChannelType.voice):
        raise ValueError(f"Unrecognized channel_type: {channel_type}")

    channel = discord.utils.get(guild.channels, category=category, type=channel_type, name=channel_name)
    created = False
    if not channel:
        message = f"Creating a new channel: {channel_name} of type {channel_type} for category: {category}"
        logger.info(message)
        create_method = (
            guild.create_text_channel if channel_type is discord.ChannelType.text else guild.create_voice_channel
        )
        channel = await create_method(channel_name, category=category, **kwargs)
        created = True

    return (channel, created)


# Discord's per-category channel cap; a full category falls back to top level.
MAX_CATEGORY_CHANNELS = 50


async def resolve_category(guild, name):
    """The category called *name* (trimmed, case-insensitive), created if the
    guild has none; None for a blank name (top level)."""
    name = (name or "").strip()
    if not name:
        return None
    found = next((c for c in guild.categories if c.name.lower() == name.lower()), None)
    return found if found is not None else await guild.create_category(name[:100])


def with_room(category):
    """*category* if it can take another channel, else None (also for None)."""
    if category is None or len(category.channels) >= MAX_CATEGORY_CHANNELS:
        return None
    return category

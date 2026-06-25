"""Ctx-free Discord channel/category adapter helpers (M3.5).

Extracted from bot.cogs.puzzles.Puzzles so the service layer can use them
without a cog or ctx dependency.
"""
import logging

import discord


logger = logging.getLogger(__name__)

# --- Constants (re-exposed so callers don't reach into the cog) ---

SOLVE_CATEGORY = False
SOLVE_DIVIDER = "———solved———"
MAX_CHANNELS_PER_GUILD = 500
MAX_CHANNELS_PER_CATEGORY = 50
MAX_CATEGORIES = 50
DELETE_REASON = "bot-delete"


# ------------------------------------------------------------------ helpers
def clean_name(name):
    """Cleanup name to be appropriate for discord channel."""
    name = name.strip()
    if (name[0] == name[-1]) and name.startswith(("'", '"')):
        name = name[1:-1]
    return "-".join(name.lower().split())


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
    category: discord.CategoryChannel,
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


# ------------------------------------------------------------------ cleanup
async def delete_category_with_channels(category):
    """Best-effort teardown of a category and all its child channels."""
    try:
        for ch in list(category.channels):
            await ch.delete(reason=DELETE_REASON)
        await category.delete(reason=DELETE_REASON)
    except Exception:
        logger.exception("delete_category_with_channels: rollback failed")

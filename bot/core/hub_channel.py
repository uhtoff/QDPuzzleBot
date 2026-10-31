"""The hub's admin channel: the channel named by the hub's `discord_bot_channel`
setting, used only if @everyone can't see it. Invite links and access-change
notes go here, so a channel everyone can read is refused."""
import logging

import discord

from bot.store import GuildSettingsDb

logger = logging.getLogger(__name__)


def find(hub):
    """The hub's admin channel, or (None, reason it can't be used)."""
    settings = GuildSettingsDb.get(hub.id)
    if settings is None:
        return None, f"{hub.name} has no guild settings"
    name = settings.discord_bot_channel
    if not name:
        return None, f"{hub.name} has no bot channel set"
    channel = discord.utils.get(hub.text_channels, name=name)
    if channel is None:
        return None, f"{hub.name} has no #{name} channel"
    if channel.permissions_for(hub.default_role).view_channel:
        return None, f"#{name} is visible to everyone on {hub.name}"
    return channel, ""


async def post(hub, text):
    """Send `text` to the hub's admin channel. Returns whether it was sent; never raises."""
    if hub is None:
        return False
    try:
        channel, reason = find(hub)
        if channel is None:
            logger.warning("hub admin channel unavailable on guild %s: %s", hub.id, reason)
            return False
        await channel.send(text)
        return True
    except discord.HTTPException:
        logger.exception("could not post to the hub admin channel on guild %s", hub.id)
        return False
    except Exception:
        logger.exception("unexpected error posting to the hub admin channel on guild %s", hub.id)
        return False

"""Post into a puzzle channel as a dashboard member, via a per-channel webhook.

A webhook bypasses Discord's own permission checks, so callers must enforce the
member's channel permissions themselves before calling `send_as`.
"""
import asyncio

import discord

RELAY_NAME = "Puzzbot relay"

# channel id -> webhook; dropped when Discord says the webhook is gone.
_hooks: dict[int, discord.Webhook] = {}
# channel id -> lock serialising hook discovery/creation for that channel.
_locks: dict[int, asyncio.Lock] = {}


def is_relay_webhook(webhook_id) -> bool:
    """True when `webhook_id` is a cached relay hook (relay_webhook caches the
    hook before any send, so it is known before the gateway event arrives)."""
    if webhook_id is None:
        return False
    return any(getattr(h, "id", None) == webhook_id for h in _hooks.values())


async def relay_webhook(channel) -> discord.Webhook:
    """The channel's own "Puzzbot relay" webhook, created if absent.

    Only a hook with exactly that name, created by this bot and carrying a token
    is reused; never any other hook (the archiver's, or another bot's).
    """
    cached = _hooks.get(channel.id)
    if cached is not None:
        return cached
    # Serialise per channel so concurrent first replies cannot each create a hook.
    async with _locks.setdefault(channel.id, asyncio.Lock()):
        cached = _hooks.get(channel.id)
        if cached is not None:
            return cached
        bot_id = channel.guild.me.id
        for hook in await channel.webhooks():
            if (hook.name == RELAY_NAME and hook.user is not None
                    and hook.user.id == bot_id and hook.token):
                _hooks[channel.id] = hook
                return hook
        hook = await channel.create_webhook(name=RELAY_NAME)
        _hooks[channel.id] = hook
        return hook


async def send_as(channel, member, content: str) -> int:
    """Send `content` (already validated) under the member's name and avatar;
    returns the new message id. Mentions are suppressed."""
    async def _send(hook):
        return await hook.send(
            content,
            username=member.display_name,
            avatar_url=member.display_avatar.url,
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
        )

    hook = await relay_webhook(channel)
    try:
        msg = await _send(hook)
    except discord.NotFound:
        # Webhook deleted behind our back: rebuild once and retry once.
        _hooks.pop(channel.id, None)
        msg = await _send(await relay_webhook(channel))
    return msg.id

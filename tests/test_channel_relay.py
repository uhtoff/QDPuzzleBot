"""bot.core.channel_relay: the relay webhook and posting as a member."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.core import channel_relay

BOT_ID = 99


def _hook(name, creator_id, token="tok"):
    user = None if creator_id is None else SimpleNamespace(id=creator_id)
    return SimpleNamespace(name=name, user=user, token=token, send=AsyncMock())


def _channel(hooks, cid=1):
    ch = MagicMock()
    ch.id = cid
    ch.guild.me.id = BOT_ID
    ch.webhooks = AsyncMock(return_value=hooks)
    ch.create_webhook = AsyncMock(return_value=_hook("Puzzbot relay", BOT_ID))
    return ch


@pytest.fixture(autouse=True)
def _clear_cache():
    channel_relay._hooks.clear()
    yield
    channel_relay._hooks.clear()


@pytest.mark.asyncio
async def test_relay_webhook_reuses_named_hook_and_ignores_archiver():
    archiver = _hook("Archiver", BOT_ID)
    foreign = _hook("Puzzbot relay", 5)           # right name, someone else's
    anon = _hook("Puzzbot relay", None)           # no creator info
    tokenless = _hook("Puzzbot relay", BOT_ID, token=None)
    ours = _hook("Puzzbot relay", BOT_ID)
    ch = _channel([archiver, foreign, anon, tokenless, ours])
    assert await channel_relay.relay_webhook(ch) is ours
    ch.create_webhook.assert_not_awaited()

    # No acceptable hook: create one named "Puzzbot relay", never reuse another.
    ch2 = _channel([archiver, foreign, anon, tokenless], cid=2)
    hook = await channel_relay.relay_webhook(ch2)
    ch2.create_webhook.assert_awaited_once_with(name="Puzzbot relay")
    assert hook is ch2.create_webhook.return_value


@pytest.mark.asyncio
async def test_send_as_disables_mentions_and_uses_display_name():
    ours = _hook("Puzzbot relay", BOT_ID)
    ours.send.return_value = SimpleNamespace(id=4242)
    ch = _channel([ours])
    member = SimpleNamespace(display_name="Alice", display_avatar=SimpleNamespace(url="http://a/x.png"))
    assert await channel_relay.send_as(ch, member, "hi @everyone") == 4242
    kwargs = ours.send.await_args.kwargs
    assert ours.send.await_args.args == ("hi @everyone",) or kwargs.get("content") == "hi @everyone"
    assert kwargs["username"] == "Alice"
    assert kwargs["avatar_url"] == "http://a/x.png"
    assert kwargs["wait"] is True
    am = kwargs["allowed_mentions"]
    assert am.everyone is False and am.users is False and am.roles is False


@pytest.mark.asyncio
async def test_send_as_recreates_once_on_not_found_then_retries():
    stale = _hook("Puzzbot relay", BOT_ID)
    stale.send.side_effect = discord.NotFound(MagicMock(status=404), "Unknown Webhook")
    ch = _channel([stale])
    fresh = ch.create_webhook.return_value
    fresh.send.return_value = SimpleNamespace(id=7)
    member = SimpleNamespace(display_name="A", display_avatar=SimpleNamespace(url="u"))
    ch.webhooks.side_effect = [[stale], []]
    assert await channel_relay.send_as(ch, member, "x") == 7
    ch.create_webhook.assert_awaited_once()
    assert channel_relay._hooks[ch.id] is fresh


@pytest.mark.asyncio
async def test_send_as_not_found_twice_propagates():
    ch = _channel([])
    ch.create_webhook.return_value.send.side_effect = discord.NotFound(MagicMock(status=404), "x")
    member = SimpleNamespace(display_name="A", display_avatar=SimpleNamespace(url="u"))
    with pytest.raises(discord.NotFound):
        await channel_relay.send_as(ch, member, "x")
    assert ch.create_webhook.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_first_replies_create_one_hook():
    import asyncio
    ch = _channel([])

    async def slow_create(**kw):
        await asyncio.sleep(0.01)
        return _hook("Puzzbot relay", BOT_ID)
    ch.create_webhook = AsyncMock(side_effect=slow_create)
    a, b = await asyncio.gather(channel_relay.relay_webhook(ch),
                                channel_relay.relay_webhook(ch))
    ch.create_webhook.assert_awaited_once()
    assert a is b


def test_is_relay_webhook_knows_cached_ids_only():
    channel_relay._hooks[1] = SimpleNamespace(id=5)
    assert channel_relay.is_relay_webhook(5)
    assert not channel_relay.is_relay_webhook(6)
    assert not channel_relay.is_relay_webhook(None)

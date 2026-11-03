"""The hub's admin channel: the channel named by the hub's `discord_bot_channel`
setting, and only if @everyone can't see it."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hub_channel

HUB_ID = 1000


def _channel(name, visible):
    ch = MagicMock()
    ch.name = name
    ch.send = AsyncMock()
    ch.permissions_for.return_value.view_channel = visible
    return ch


def _hub(channels):
    hub = MagicMock()
    hub.id = HUB_ID
    hub.name = "The Hub"
    hub.text_channels = channels
    return hub


def _settings(name):
    s = MagicMock()
    s.discord_bot_channel = name
    return s


async def test_posts_in_the_hubs_command_channel():
    ch = _channel("bot-set-up", visible=False)
    other = _channel("general", visible=True)
    hub = _hub([other, ch])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.return_value = _settings("bot-set-up")
        assert await hub_channel.post(hub, "hello") is True
    ch.send.assert_awaited_once_with("hello")
    other.send.assert_not_awaited()
    ch.permissions_for.assert_called_with(hub.default_role)


@pytest.mark.parametrize("settings", [None, _settings(""), _settings("nonexistent")])
async def test_unset_or_missing_channel_posts_nothing(settings):
    ch = _channel("bot-set-up", visible=False)
    hub = _hub([ch])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.return_value = settings
        assert await hub_channel.post(hub, "hello") is False
        found, reason = hub_channel.find(hub)
    assert found is None
    assert reason
    ch.send.assert_not_awaited()


async def test_a_channel_everyone_can_see_is_refused():
    ch = _channel("bot-set-up", visible=True)
    hub = _hub([ch])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.return_value = _settings("bot-set-up")
        assert await hub_channel.post(hub, "hello") is False
        found, reason = hub_channel.find(hub)
    assert found is None
    assert "visible to everyone" in reason
    assert "#bot-set-up" in reason
    ch.send.assert_not_awaited()


async def test_a_failed_send_returns_false():
    ch = _channel("bot-set-up", visible=False)
    ch.send.side_effect = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no")
    hub = _hub([ch])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.return_value = _settings("bot-set-up")
        assert await hub_channel.post(hub, "hello") is False


async def test_no_hub_returns_false():
    assert await hub_channel.post(None, "hello") is False


async def test_a_settings_lookup_error_returns_false():
    hub = _hub([_channel("bot-set-up", visible=False)])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.side_effect = RuntimeError("db down")
        assert await hub_channel.post(hub, "hello") is False


async def test_a_non_http_send_error_returns_false():
    ch = _channel("bot-set-up", visible=False)
    ch.send.side_effect = TimeoutError()
    hub = _hub([ch])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.return_value = _settings("bot-set-up")
        assert await hub_channel.post(hub, "hello") is False


async def test_the_unavailable_warning_does_not_log_the_text(caplog):
    hub = _hub([_channel("bot-set-up", visible=True)])
    with patch("bot.core.hub_channel.GuildSettingsDb") as db:
        db.get.return_value = _settings("bot-set-up")
        with caplog.at_level("WARNING"):
            await hub_channel.post(hub, "https://discord.gg/SECRETINVITE")
    assert caplog.records
    assert "SECRETINVITE" not in caplog.text
    assert "visible to everyone" in caplog.text


async def test_post_passes_allowed_mentions_only_when_given():
    from unittest.mock import AsyncMock, MagicMock, patch
    import discord
    ch = MagicMock(); ch.send = AsyncMock()
    with patch.object(hub_channel, "find", return_value=(ch, "")):
        assert await hub_channel.post(MagicMock(), "a") is True
        ch.send.assert_awaited_once_with("a")
        ch.send.reset_mock()
        am = discord.AllowedMentions.none()
        await hub_channel.post(MagicMock(), "b", allowed_mentions=am)
        ch.send.assert_awaited_once_with("b", allowed_mentions=am)

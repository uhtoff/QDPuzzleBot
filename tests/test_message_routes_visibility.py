from unittest.mock import AsyncMock, MagicMock, patch


async def test_channel_messages_visible_uses_hunt_gate():
    from bot.web.api import _channel_messages_visible
    with patch("bot.web.api.data.get_hunt_by_channel_id", new=AsyncMock(return_value={"uid": "H"})), \
         patch("bot.web.api.data.get_guilds_map", new=AsyncMock(return_value={})), \
         patch("bot.web.api.hunt_visible", return_value=True) as hv:
        ok = await _channel_messages_visible(100, membership={42}, roles_by_guild={})
    assert ok is True and hv.called


async def test_channel_messages_hidden_when_no_hunt():
    from bot.web.api import _channel_messages_visible
    with patch("bot.web.api.data.get_hunt_by_channel_id", new=AsyncMock(return_value=None)):
        ok = await _channel_messages_visible(100, membership={42}, roles_by_guild={})
    assert ok is False

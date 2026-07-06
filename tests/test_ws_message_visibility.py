from unittest.mock import MagicMock, patch


async def test_message_event_uses_channel_permission_gate():
    from bot.web import api
    channel = MagicMock(); channel.permissions_for.return_value = MagicMock(view_channel=True)
    guild = MagicMock(); guild.get_channel.return_value = channel
    guild.get_member.return_value = MagicMock()
    bot = MagicMock(); bot.get_guild.return_value = guild
    ev = {"type": "message", "channel_id": "100", "guild_id": 42}
    with patch("bot.web.api.bot_app", MagicMock(return_value=MagicMock(bot=bot))):
        ok = await api._activity_event_visible(ev, user_id=7, guild_id=42)
    assert ok is True

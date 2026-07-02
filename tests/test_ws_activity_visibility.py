"""Tests for the per-hunt visibility gates on the /ws/activity live feed (#38).

Guild membership alone is too coarse: one guild can host several teams whose
channels are gated by a per-hunt Discord role, not by guild membership.
_activity_event_visible and _board_event_visible close that gap.
"""
from unittest.mock import AsyncMock, MagicMock, patch


class TestActivityEventVisible:
    async def test_visible_when_member_can_view_channel(self):
        from bot.web.api import _activity_event_visible

        member = MagicMock()
        channel = MagicMock()
        channel.permissions_for.return_value = MagicMock(view_channel=True)
        guild = MagicMock()
        guild.get_channel.return_value = channel
        guild.get_member.return_value = member
        bot = MagicMock()
        bot.get_guild.return_value = guild

        with patch("bot.web.api.current_app", MagicMock(bot=bot)):
            result = await _activity_event_visible({"channel_id": "111"}, user_id=7, guild_id=42)

        assert result is True
        channel.permissions_for.assert_called_once_with(member)

    async def test_hidden_when_channel_overwrite_denies_view(self):
        from bot.web.api import _activity_event_visible

        channel = MagicMock()
        channel.permissions_for.return_value = MagicMock(view_channel=False)
        guild = MagicMock()
        guild.get_channel.return_value = channel
        guild.get_member.return_value = MagicMock()
        bot = MagicMock()
        bot.get_guild.return_value = guild

        with patch("bot.web.api.current_app", MagicMock(bot=bot)):
            result = await _activity_event_visible({"channel_id": "111"}, user_id=7, guild_id=42)

        assert result is False

    async def test_hidden_when_guild_not_cached(self):
        from bot.web.api import _activity_event_visible

        bot = MagicMock()
        bot.get_guild.return_value = None

        with patch("bot.web.api.current_app", MagicMock(bot=bot)):
            result = await _activity_event_visible({"channel_id": "111"}, user_id=7, guild_id=42)

        assert result is False

    async def test_hidden_when_channel_deleted_or_uncached(self):
        from bot.web.api import _activity_event_visible

        guild = MagicMock()
        guild.get_channel.return_value = None
        bot = MagicMock()
        bot.get_guild.return_value = guild

        with patch("bot.web.api.current_app", MagicMock(bot=bot)):
            result = await _activity_event_visible({"channel_id": "111"}, user_id=7, guild_id=42)

        assert result is False

    async def test_falls_back_to_fetch_member_when_not_cached(self):
        from bot.web.api import _activity_event_visible

        fetched_member = MagicMock()
        channel = MagicMock()
        channel.permissions_for.return_value = MagicMock(view_channel=True)
        guild = MagicMock()
        guild.get_channel.return_value = channel
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(return_value=fetched_member)
        bot = MagicMock()
        bot.get_guild.return_value = guild

        with patch("bot.web.api.current_app", MagicMock(bot=bot)):
            result = await _activity_event_visible({"channel_id": "111"}, user_id=7, guild_id=42)

        assert result is True
        channel.permissions_for.assert_called_once_with(fetched_member)

    async def test_hidden_when_user_no_longer_a_member(self):
        from bot.web.api import _activity_event_visible

        guild = MagicMock()
        guild.get_channel.return_value = MagicMock()
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(side_effect=Exception("not found"))
        bot = MagicMock()
        bot.get_guild.return_value = guild

        with patch("bot.web.api.current_app", MagicMock(bot=bot)):
            result = await _activity_event_visible({"channel_id": "111"}, user_id=7, guild_id=42)

        assert result is False


class TestBoardEventVisible:
    async def test_visible_when_hunt_visible_to_user(self):
        from bot.web.api import _board_event_visible

        hunt = {"guild_id": 1, "archived": False, "role_id": 0}
        with patch("bot.web.api.data.get_hunt_by_uid", new=AsyncMock(return_value=hunt)), \
             patch("bot.web.api.data.get_guilds_map",
                   new=AsyncMock(return_value={1: {"guild_snowflake": 42}})):
            result = await _board_event_visible({"uid": "HUNT1"}, membership={42}, roles_by_guild={})

        assert result is True

    async def test_hidden_when_role_gate_fails(self):
        from bot.web.api import _board_event_visible

        hunt = {"guild_id": 1, "archived": False, "role_id": 99}
        with patch("bot.web.api.data.get_hunt_by_uid", new=AsyncMock(return_value=hunt)), \
             patch("bot.web.api.data.get_guilds_map",
                   new=AsyncMock(return_value={1: {"guild_snowflake": 42}})):
            result = await _board_event_visible({"uid": "HUNT1"}, membership={42}, roles_by_guild={42: set()})

        assert result is False

    async def test_hidden_when_hunt_unknown(self):
        from bot.web.api import _board_event_visible

        with patch("bot.web.api.data.get_hunt_by_uid", new=AsyncMock(return_value=None)), \
             patch("bot.web.api.data.get_guilds_map", new=AsyncMock(return_value={})):
            result = await _board_event_visible({"uid": "GONE"}, membership={42}, roles_by_guild={})

        assert result is False

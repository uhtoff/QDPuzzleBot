"""Tests for hunt_visible in bot/web/auth.py.

hunt_visible(hunt, membership, roles_by_guild, guilds_map) → bool

Rules:
  - guild must exist in guilds_map and user must be a member of that guild
  - archived hunts: visible to any member
  - active hunt with role_id == 0: visible to any member (legacy)
  - active hunt with role_id != 0: visible only if user holds that role
"""

from bot.web.auth import hunt_visible


def _guilds_map(guild_snowflake="GUILD1"):
    return {1: {"guild_snowflake": guild_snowflake, "guild_name": "Test Guild"}}


def _hunt(archived=False, role_id=0, guild_id=1):
    return {"guild_id": guild_id, "archived": archived, "role_id": role_id}


class TestHuntVisibleGuildGating:
    def test_unknown_guild_id_hidden(self):
        hunt = _hunt(guild_id=99)
        assert hunt_visible(hunt, {"GUILD1"}, {"GUILD1": {42}}, _guilds_map()) is False

    def test_user_not_in_guild_hidden(self):
        hunt = _hunt()
        assert hunt_visible(hunt, set(), {}, _guilds_map()) is False

    def test_user_in_guild_archived_visible(self):
        hunt = _hunt(archived=True)
        assert hunt_visible(hunt, {"GUILD1"}, {}, _guilds_map()) is True


class TestHuntVisibleActiveLegacy:
    def test_role_id_zero_any_member_visible(self):
        hunt = _hunt(archived=False, role_id=0)
        assert hunt_visible(hunt, {"GUILD1"}, {}, _guilds_map()) is True

    def test_role_id_none_any_member_visible(self):
        hunt = _hunt(archived=False, role_id=None)
        assert hunt_visible(hunt, {"GUILD1"}, {}, _guilds_map()) is True


class TestHuntVisibleActiveWithRole:
    def test_user_holds_required_role_visible(self):
        hunt = _hunt(archived=False, role_id=42)
        assert hunt_visible(hunt, {"GUILD1"}, {"GUILD1": {42}}, _guilds_map()) is True

    def test_user_lacks_required_role_hidden(self):
        hunt = _hunt(archived=False, role_id=42)
        assert hunt_visible(hunt, {"GUILD1"}, {"GUILD1": {99}}, _guilds_map()) is False

    def test_user_has_no_roles_hidden(self):
        hunt = _hunt(archived=False, role_id=42)
        assert hunt_visible(hunt, {"GUILD1"}, {}, _guilds_map()) is False

    def test_user_holds_role_in_different_guild_hidden(self):
        hunt = _hunt(archived=False, role_id=42)
        assert hunt_visible(hunt, {"GUILD1"}, {"GUILD2": {42}}, _guilds_map()) is False


class TestHuntVisibleArchivedIgnoresRole:
    def test_archived_visible_without_role(self):
        hunt = _hunt(archived=True, role_id=42)
        assert hunt_visible(hunt, {"GUILD1"}, {"GUILD1": {99}}, _guilds_map()) is True

    def test_archived_visible_with_no_roles_at_all(self):
        hunt = _hunt(archived=True, role_id=42)
        assert hunt_visible(hunt, {"GUILD1"}, {}, _guilds_map()) is True

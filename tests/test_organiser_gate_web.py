"""Web-layer Organiser-tier bypass: user_can_manage (auth.py) and
require_organiser (actions.py) both allow any guild member through when the
guild has no role named 'Organisers'. The Admin tier is never bypassed."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES
from bot.web import auth
from tests.conftest import make_web_app, start_patches, stop_patches


def _bot_with_guild(role_names):
    guild = SimpleNamespace(roles=[SimpleNamespace(name=n) for n in role_names])
    bot = MagicMock()
    bot.get_guild.return_value = guild
    return bot


class TestOrganiserTierBypassed:
    def test_true_when_organiser_tier_and_no_organisers_role(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is True

    def test_false_when_organisers_role_present(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["Organisers"]))):
            assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is False

    def test_false_for_each_accepted_spelling(self):
        for name in ("Organiser", "Organisers", "Organizer", "Organizers"):
            with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild([name]))):
                assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is False, \
                    f"{name!r} should not be bypassed"

    def test_false_for_admin_tier_regardless_of_organisers_role(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            assert auth.organiser_tier_bypassed(123, ADMIN_ROLES) is False

    def test_false_when_guild_unknown(self):
        bot = MagicMock()
        bot.get_guild.return_value = None
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=bot)):
            assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is False


class TestUserCanManageBypass:
    async def test_bypassed_for_member_without_organiser_role(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value={"everyone"})):
            assert await auth.user_can_manage(1, 123) is True

    async def test_non_member_still_denied_even_when_gate_inactive(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value=None)):
            assert await auth.user_can_manage(1, 123) is False

    async def test_admin_tier_still_requires_role_when_organisers_role_absent(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value={"everyone"})):
            assert await auth.user_can_manage(1, 123, roles=ADMIN_ROLES) is False

    async def test_existing_behaviour_preserved_when_organisers_role_present(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["Organisers"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value={"everyone"})):
            assert await auth.user_can_manage(1, 123) is False


from bot.web import actions


class TestRequireOrganiserBypass:
    # require_organiser's error paths call Quart's jsonify(), which needs an
    # active app context; a bare Quart app is enough since we only need
    # jsonify() to resolve, not a running server.
    async def test_bypassed_for_member_without_organiser_role(self):
        from quart import Quart

        async with Quart(__name__).app_context():
            with patch("bot.web.actions.current_user", return_value={"id": 1}), \
                 patch("bot.web.actions.resolve_role_names", AsyncMock(return_value={"everyone"})), \
                 patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
                assert await actions.require_organiser(123) is None

    async def test_non_member_still_denied(self):
        from quart import Quart

        async with Quart(__name__).app_context():
            with patch("bot.web.actions.current_user", return_value={"id": 1}), \
                 patch("bot.web.actions.resolve_role_names", AsyncMock(return_value=None)), \
                 patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
                result = await actions.require_organiser(123)
                assert result is not None
                _, status = result
                assert status == 403

    async def test_require_admin_still_denied_when_organisers_role_absent(self):
        from quart import Quart

        async with Quart(__name__).app_context():
            with patch("bot.web.actions.current_user", return_value={"id": 1}), \
                 patch("bot.web.actions.resolve_role_names", AsyncMock(return_value={"everyone"})), \
                 patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
                result = await actions.require_admin(123)
                assert result is not None
                _, status = result
                assert status == 403

    async def test_existing_behaviour_preserved_when_organisers_role_present(self):
        from quart import Quart

        async with Quart(__name__).app_context():
            with patch("bot.web.actions.current_user", return_value={"id": 1}), \
                 patch("bot.web.actions.resolve_role_names", AsyncMock(return_value={"everyone"})), \
                 patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["Organisers"]))):
                result = await actions.require_organiser(123)
                assert result is not None
                _, status = result
                assert status == 403


class TestHuntDetailOrganiserGateField:
    @pytest.fixture
    def hunt_web(self):
        app = make_web_app("api")

        guild = SimpleNamespace(roles=[SimpleNamespace(name="everyone")])
        bot = MagicMock()
        bot.get_guild.return_value = guild

        hunt_row = {
            "id": 10, "uid": "U1", "name": "Hunt", "url": "http://x",
            "archived": False, "guild_id": 1, "role_id": 0,
            "google_sheet_id": None, "solved_sheet_id": None,
        }

        patches = [
            patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
            patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(hunt_row, {"guild_snowflake": 123}))),
            patch("bot.web.api.data.get_hunt_puzzles", AsyncMock(return_value=[])),
            patch("bot.web.api.data.get_rollups_by_tag_group", AsyncMock(return_value={})),
            patch("bot.web.api.data.get_all_tag_groups", AsyncMock(return_value=[])),
            patch("bot.web.api.data.build_board", return_value=[]),
            patch("bot.web.api.user_can_manage", AsyncMock(return_value=False)),
            patch("bot.web.api.bot_app", return_value=MagicMock(bot=bot)),
        ]
        start_patches(patches)
        try:
            yield SimpleNamespace(client=app.test_client(), guild=guild)
        finally:
            stop_patches(patches)

    async def test_true_when_organisers_role_present(self, hunt_web):
        hunt_web.guild.roles = [SimpleNamespace(name="Organisers")]
        resp = await hunt_web.client.get("/api/hunts/U1")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["hunt"]["organiser_gate_active"] is True

    async def test_false_when_no_organisers_role(self, hunt_web):
        hunt_web.guild.roles = [SimpleNamespace(name="everyone")]
        resp = await hunt_web.client.get("/api/hunts/U1")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["hunt"]["organiser_gate_active"] is False

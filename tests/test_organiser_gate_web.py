"""Web-layer Organiser-tier bypass: user_can_manage (auth.py) and
require_organiser (actions.py) both allow any guild member through when the
guild has no role named 'Organisers'. The Admin tier is never bypassed."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES
from bot.web import auth


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

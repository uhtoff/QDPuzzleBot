"""M8 phase 3: the web edge gates on configured role IDS.

Sibling of tests/test_organiser_gate_web.py, which pins the UNCONFIGURED
(bootstrap) half where names still decide. Here the guild has opted in, so a role
merely *named* `admin` — which anyone with Manage Roles can mint and self-assign —
must not carry the tier.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart

from bot.web import actions, auth

REAL_ADMIN_ID = 700
IMPOSTOR_ID = 999
EVERYONE_ID = 1000


def _bot():
    """A guild holding the real admin role, plus an impostor role NAMED 'admin'."""
    guild = SimpleNamespace(id=123, roles=[
        SimpleNamespace(id=EVERYONE_ID, name="everyone"),
        SimpleNamespace(id=REAL_ADMIN_ID, name="Hunt Runners"),
        SimpleNamespace(id=IMPOSTOR_ID, name="admin"),
    ])
    bot = MagicMock()
    bot.get_guild.return_value = guild
    return bot


@pytest.fixture(autouse=True)
def _configured():
    """Guild 123 has opted in: only role 700 is admin, only 800 is organiser."""
    db = MagicMock()
    db.get_cached.return_value = SimpleNamespace(
        admin_role_ids=str(REAL_ADMIN_ID), organiser_role_ids="800")
    with patch("bot.store.GuildSettingsDb", db):
        yield


async def _require_admin_status(member_role_ids):
    async with Quart(__name__).app_context():
        with patch("bot.web.actions.current_user", return_value={"id": 1}), \
             patch("bot.web.actions.resolve_member_role_ids",
                   AsyncMock(return_value=member_role_ids)), \
             patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot())):
            result = await actions.require_admin(123)
    return None if result is None else result[1]


async def test_impostor_role_named_admin_is_refused():
    assert await _require_admin_status({EVERYONE_ID, IMPOSTOR_ID}) == 403


async def test_configured_admin_role_is_allowed():
    assert await _require_admin_status({EVERYONE_ID, REAL_ADMIN_ID}) is None


async def test_user_can_manage_ignores_the_impostor_role():
    from bot.core.constants import ADMIN_ROLES

    with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot())), \
         patch("bot.web.auth.resolve_member_role_ids",
               AsyncMock(return_value={EVERYONE_ID, IMPOSTOR_ID})):
        assert await auth.user_can_manage(1, 123, roles=ADMIN_ROLES) is False


async def test_user_can_manage_accepts_the_configured_role():
    from bot.core.constants import ADMIN_ROLES

    with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot())), \
         patch("bot.web.auth.resolve_member_role_ids",
               AsyncMock(return_value={EVERYONE_ID, REAL_ADMIN_ID})):
        assert await auth.user_can_manage(1, 123, roles=ADMIN_ROLES) is True


async def test_organiser_tier_is_not_bypassed_when_ids_are_configured():
    """The guild has no Organiser-NAMED role at all. Before M8 that read as
    'no organiser role exists' and opened the tier to every member; the gate now
    follows the configured ids, so it stays shut."""
    from bot.core.constants import ORGANISER_ROLES

    with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot())):
        assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is False

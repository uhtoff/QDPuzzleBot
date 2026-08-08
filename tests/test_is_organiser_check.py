"""is_organiser() bypasses Organiser-tier gating when the guild has nothing
standing for the Organiser tier; Admin-tier roles still satisfy the check when
the gate is active, since ORGANISER_ROLES includes ADMIN_ROLES.

Every guild here is UNCONFIGURED (no stored role ids), the bootstrap state where
the NAMES still decide — so these expectations are unchanged by M8. The id-keyed
half is tests/test_is_admin_check.py."""
from unittest.mock import MagicMock, patch

import pytest
from discord.ext import commands

from bot.cogs.puzzles import is_organiser
from tests.conftest import make_ctx


@pytest.fixture(autouse=True)
def _unconfigured():
    db = MagicMock()
    db.get_cached.return_value = None
    with patch("bot.store.GuildSettingsDb", db):
        yield


_next_id = iter(range(1000, 2000))


def _role(name):
    role = MagicMock()
    role.name = name
    role.id = next(_next_id)
    return role


async def test_bypasses_when_no_organisers_role():
    ctx = make_ctx()
    ctx.guild.roles = [_role("everyone")]
    ctx.author.roles = []
    assert await is_organiser().predicate(ctx) is True


async def test_allows_organiser_when_organisers_role_exists_and_held():
    ctx = make_ctx()
    organisers = _role("Organisers")
    ctx.guild.roles = [organisers]
    ctx.author.roles = [organisers]
    assert await is_organiser().predicate(ctx) is True


async def test_allows_admin_role_when_organisers_role_exists():
    ctx = make_ctx()
    mod = _role("mod")
    ctx.guild.roles = [_role("Organisers"), mod]
    ctx.author.roles = [mod]
    assert await is_organiser().predicate(ctx) is True


async def test_denies_regular_user_when_organisers_role_exists():
    ctx = make_ctx()
    ctx.guild.roles = [_role("Organisers")]
    ctx.author.roles = [_role("everyone")]
    with pytest.raises(commands.MissingAnyRole):
        await is_organiser().predicate(ctx)


async def test_raises_no_private_message_outside_guild():
    ctx = make_ctx()
    ctx.guild = None
    with pytest.raises(commands.NoPrivateMessage):
        await is_organiser().predicate(ctx)

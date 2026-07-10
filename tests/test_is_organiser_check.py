"""is_organiser() bypasses Organiser-tier gating when the guild has no role
literally named 'Organisers'; Admin-tier role names (Moderator/mod/admin)
still satisfy the check when the gate is active, since ORGANISER_ROLES
includes ADMIN_ROLES."""
from unittest.mock import MagicMock

import pytest
from discord.ext import commands

from bot.cogs.puzzles import is_organiser
from tests.conftest import make_ctx


def _role(name):
    role = MagicMock()
    role.name = name
    return role


async def test_bypasses_when_no_organisers_role():
    ctx = make_ctx()
    ctx.guild.roles = [_role("everyone")]
    ctx.author.roles = []
    assert await is_organiser().predicate(ctx) is True


async def test_allows_organiser_when_organisers_role_exists_and_held():
    ctx = make_ctx()
    ctx.guild.roles = [_role("Organisers")]
    ctx.author.roles = [_role("Organisers")]
    assert await is_organiser().predicate(ctx) is True


async def test_allows_admin_role_when_organisers_role_exists():
    ctx = make_ctx()
    ctx.guild.roles = [_role("Organisers")]
    ctx.author.roles = [_role("mod")]
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

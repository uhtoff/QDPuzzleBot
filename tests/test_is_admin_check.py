"""M8 phase 4: the bang edge gates on role ids.

The 16 admin-gated bang commands used `commands.has_any_role(*ADMIN_ROLES)`,
discord.py's own NAME-matching check, so a member with Manage Roles could mint a
role called `admin` and run !delete_hunt. They now go through `is_admin()`, which
asks role_gate.

Sibling of tests/test_is_organiser_check.py, which covers the unconfigured
(bootstrap) state where names still decide.
"""

from unittest.mock import MagicMock, patch

import pytest
from discord.ext import commands

from bot.cogs.puzzles import is_admin
from tests.conftest import make_ctx

REAL_ADMIN_ID = 700
IMPOSTOR_ID = 999
EVERYONE_ID = 1000


def _role(id, name):
    role = MagicMock()
    role.name = name
    role.id = id
    return role


EVERYONE = _role(EVERYONE_ID, "everyone")
RUNNERS = _role(REAL_ADMIN_ID, "Hunt Runners")
IMPOSTOR = _role(IMPOSTOR_ID, "admin")


def _ctx(*author_roles):
    ctx = make_ctx()
    ctx.guild.id = 123
    ctx.guild.roles = [EVERYONE, RUNNERS, IMPOSTOR]
    ctx.author.roles = list(author_roles)
    return ctx


def _settings(admin_role_ids):
    db = MagicMock()
    db.get_cached.return_value = MagicMock(
        admin_role_ids=admin_role_ids, organiser_role_ids="")
    return patch("bot.store.GuildSettingsDb", db)


async def test_impostor_role_named_admin_is_refused_when_configured():
    with _settings(str(REAL_ADMIN_ID)):
        with pytest.raises(commands.MissingAnyRole):
            await is_admin().predicate(_ctx(EVERYONE, IMPOSTOR))


async def test_configured_role_is_allowed():
    with _settings(str(REAL_ADMIN_ID)):
        assert await is_admin().predicate(_ctx(EVERYONE, RUNNERS)) is True


async def test_unconfigured_guild_still_matches_by_name():
    """Bootstrap: nothing stored, so the role NAMED 'admin' does grant the tier —
    the pre-M8 behaviour every guild keeps until it opts in."""
    with _settings(""):
        assert await is_admin().predicate(_ctx(EVERYONE, IMPOSTOR)) is True


async def test_unconfigured_guild_still_denies_a_plain_member():
    with _settings(""):
        with pytest.raises(commands.MissingAnyRole):
            await is_admin().predicate(_ctx(EVERYONE))


async def test_admin_tier_is_never_bypassed():
    """Unlike the Organiser tier, a guild with nothing standing for Admin denies
    everyone rather than opening up."""
    ctx = make_ctx()
    ctx.guild.id = 123
    ctx.guild.roles = [EVERYONE]        # no admin-named role at all
    ctx.author.roles = [EVERYONE]
    with _settings(""):
        with pytest.raises(commands.MissingAnyRole):
            await is_admin().predicate(ctx)


async def test_raises_no_private_message_outside_guild():
    ctx = make_ctx()
    ctx.guild = None
    with pytest.raises(commands.NoPrivateMessage):
        await is_admin().predicate(ctx)

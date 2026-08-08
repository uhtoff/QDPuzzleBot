"""M8 phase 5: the commands that configure a guild's authorisation role ids.

`!set_admin_roles` / `!set_organiser_roles` are how a guild opts out of name
matching; `!show_auth_roles` says which state it is in. Setting admin roles you
do not hold locks you out of the very commands that would undo it, so that is
refused.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import make_cog, make_ctx

RUNNERS_ID = 700
HELPERS_ID = 800


def _role(id, name):
    role = MagicMock()
    role.id = id
    role.name = name
    role.mention = f"<@&{id}>"
    return role


RUNNERS = _role(RUNNERS_ID, "Hunt Runners")
HELPERS = _role(HELPERS_ID, "Hunt Helpers")


@pytest.fixture
def env():
    """A cog + ctx with the guild settings row stubbed, capturing commits."""
    cog = make_cog()
    ctx = make_ctx()
    ctx.send = AsyncMock()
    ctx.guild.id = 123
    ctx.guild.roles = [RUNNERS, HELPERS]
    ctx.author.roles = [RUNNERS]
    settings = SimpleNamespace(guild_id=123, admin_role_ids="", organiser_role_ids="")
    db = MagicMock()
    db.get.return_value = settings
    db.get_cached.return_value = settings
    # Both sites: the cog reads its own import, role_gate imports from bot.store.
    with patch("bot.cogs.puzzles.GuildSettingsDb", db), \
         patch("bot.store.GuildSettingsDb", db):
        yield SimpleNamespace(cog=cog, ctx=ctx, db=db, settings=settings)


async def test_set_admin_roles_stores_the_ids(env):
    await Puzzles.set_admin_roles.callback(env.cog, env.ctx, RUNNERS)
    assert env.settings.admin_role_ids == str(RUNNERS_ID)
    env.db.commit.assert_called_once_with(env.settings)


async def test_set_admin_roles_stores_several_ids(env):
    env.ctx.author.roles = [RUNNERS]
    await Puzzles.set_admin_roles.callback(env.cog, env.ctx, RUNNERS, HELPERS)
    # Order-independent, but both must be there and separately parseable.
    assert set(env.settings.admin_role_ids.split(",")) == {str(RUNNERS_ID), str(HELPERS_ID)}


async def test_set_admin_roles_refuses_to_lock_the_caller_out(env):
    """Storing admin ids the caller does not hold would leave nobody able to run
    the command that undoes it."""
    env.ctx.author.roles = [HELPERS]
    await Puzzles.set_admin_roles.callback(env.cog, env.ctx, RUNNERS)
    assert env.settings.admin_role_ids == ""
    env.db.commit.assert_not_called()


async def test_set_admin_roles_with_no_roles_clears_back_to_bootstrap(env):
    env.settings.admin_role_ids = "700"
    await Puzzles.set_admin_roles.callback(env.cog, env.ctx)
    assert env.settings.admin_role_ids == ""
    env.db.commit.assert_called_once_with(env.settings)


async def test_set_organiser_roles_writes_the_other_column(env):
    """A transposed write would hand the admin tier to organisers."""
    await Puzzles.set_organiser_roles.callback(env.cog, env.ctx, HELPERS)
    assert env.settings.organiser_role_ids == str(HELPERS_ID)
    assert env.settings.admin_role_ids == ""


async def test_set_organiser_roles_does_not_apply_the_lockout_guard(env):
    """The organiser tier is not the one that can lock an admin out, and an admin
    is an organiser anyway — so setting organiser roles you don't hold is fine."""
    env.ctx.author.roles = [RUNNERS]
    await Puzzles.set_organiser_roles.callback(env.cog, env.ctx, HELPERS)
    assert env.settings.organiser_role_ids == str(HELPERS_ID)
    env.db.commit.assert_called_once_with(env.settings)


async def test_show_auth_roles_reports_bootstrap_state(env):
    await Puzzles.show_auth_roles.callback(env.cog, env.ctx)
    said = env.ctx.send.await_args.args[0]
    assert "name" in said.lower()


async def test_show_auth_roles_names_the_configured_roles(env):
    env.settings.admin_role_ids = str(RUNNERS_ID)
    await Puzzles.show_auth_roles.callback(env.cog, env.ctx)
    said = env.ctx.send.await_args.args[0]
    assert "Hunt Runners" in said

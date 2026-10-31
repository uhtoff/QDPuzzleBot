"""M4: `!update_settings` must not be a third, unvalidated write path to the
authorisation and identity columns.

`GuildSettings.admin_role_ids` / `organiser_role_ids` are plain `str` fields, so
the command's type gate happily `setattr`s any string and commits it. That
bypasses the guild-role validation and the lockout guard that BOTH dedicated
edges enforce (`!set_admin_roles` and the dashboard's guild-settings PATCH), and
`bot.core.role_gate` treats stored ids as the ONLY grant — so junk here locks
every admin out of both surfaces, including out of the commands that would undo
it. `guild_id` / `guild_name` key the row the auth model resolves through.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.store import GuildSettings
from tests.conftest import make_cog, make_ctx


@pytest.fixture
def env():
    """A cog + ctx sitting in a guild channel, with the settings row stubbed and
    every commit captured."""
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Guild"
    settings = GuildSettings()
    settings.guild_id = 123
    settings.guild_name = "Team Hunt"
    settings.admin_role_ids = "700"
    settings.organiser_role_ids = "800"
    cog.get_guild_data = lambda ctx: settings
    cog.service.notifier.board_changed = AsyncMock()

    ctx = make_ctx()
    ctx.send = AsyncMock()
    ctx.invoked_with = "update_settings"

    db = MagicMock()
    db.get.return_value = settings
    db.get_cached.return_value = settings
    with patch("bot.cogs.puzzles.GuildSettingsDb", db):
        yield MagicMock(cog=cog, ctx=ctx, db=db, settings=settings)


@pytest.mark.parametrize("key,value,attr,before", [
    ("admin_role_ids", "999", "admin_role_ids", "700"),
    ("organiser_role_ids", "999", "organiser_role_ids", "800"),
    ("guild_id", "456", "guild_id", 123),
    ("guild_name", "Someone Else", "guild_name", "Team Hunt"),
])
async def test_update_settings_refuses_protected_keys(env, key, value, attr, before):
    """Nothing may be committed: a stored junk role id is unrecoverable without
    direct SQL, and a rewritten guild_id orphans the row."""
    await Puzzles.update_settings.callback(env.cog, env.ctx, key, value)

    env.db.commit.assert_not_called()
    assert getattr(env.settings, attr) == before
    said = env.ctx.send.await_args.args[0]
    assert key in said


async def test_refusing_admin_role_ids_points_at_the_guarded_command(env):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "admin_role_ids", "999")
    said = env.ctx.send.await_args.args[0]
    assert "!set_admin_roles" in said


async def test_refusing_organiser_role_ids_points_at_the_guarded_command(env):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "organiser_role_ids", "999")
    said = env.ctx.send.await_args.args[0]
    assert "!set_organiser_roles" in said


async def test_update_settings_still_writes_an_ordinary_key(env):
    """The refusal must not be over-broad — every other settings key is
    unaffected."""
    await Puzzles.update_settings.callback(env.cog, env.ctx, "discord_bot_emoji", ":dog:")

    assert env.settings.discord_bot_emoji == ":dog:"
    env.db.commit.assert_called_once_with(env.settings)


async def test_unknown_keys_still_report_as_unrecognized(env):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "no_such_key", "x")

    env.db.commit.assert_not_called()
    said = env.ctx.send.await_args.args[0]
    assert "Unrecognized" in said


async def test_update_settings_refuses_template_link(env):
    """The link is validated by !set_template_link; this path must not bypass it."""
    before = env.settings.template_link
    await Puzzles.update_settings.callback(env.cog, env.ctx, "template_link", "x")

    env.db.commit.assert_not_called()
    assert env.settings.template_link == before
    assert "!set_template_link" in env.ctx.send.await_args.args[0]

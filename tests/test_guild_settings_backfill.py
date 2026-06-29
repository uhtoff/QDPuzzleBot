"""Regression tests for missing guild settings rows.

A `guilds` row is only created in `Settings.on_guild_join`, which fires solely
at the moment the bot joins a server. Guilds the bot was already a member of
when the code/DB was deployed therefore had no row, so
`GuildSettingsDb.get_cached(guild_id)` returned None and the first caller to
dereference it crashed:

    AttributeError: 'NoneType' object has no attribute 'discord_bot_channel'
    (bot/cogs/puzzles.py check_is_bot_channel)

Fix: backfill rows for guilds the bot is already in at startup, and keep the
settings cache coherent after a commit so a freshly-created row is not shadowed
by a previously cached None.
"""

from unittest.mock import MagicMock, patch

from bot.cogs.settings import Settings
from bot.store.mysqldb import MySQLGuildSettingsDb
from bot.store.puzzle_settings import GuildSettings


def _make_guild(gid, name):
    g = MagicMock()
    g.id = gid
    g.name = name
    return g


async def test_on_guild_join_creates_row_when_missing():
    cog = Settings(MagicMock())
    guild = _make_guild(5, "Hunt HQ")
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    db.commit.assert_called_once()
    committed = db.commit.call_args[0][0]
    assert committed.guild_id == 5
    assert committed.guild_name == "Hunt HQ"


async def test_on_guild_join_is_idempotent_when_row_exists():
    cog = Settings(MagicMock())
    guild = _make_guild(5, "Hunt HQ")
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = GuildSettings(guild_id=5)
        await cog.on_guild_join(guild)
    db.commit.assert_not_called()


async def test_on_ready_backfills_only_guilds_missing_a_row():
    bot = MagicMock()
    bot.guilds = [_make_guild(1, "a"), _make_guild(2, "b")]
    cog = Settings(bot)
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.side_effect = lambda gid: GuildSettings(guild_id=2) if gid == 2 else None
        await cog.on_ready()
    db.commit.assert_called_once()
    assert db.commit.call_args[0][0].guild_id == 1


def test_commit_refreshes_cache_so_new_row_not_shadowed_by_none():
    mydb = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 7
    mydb.cursor.return_value = cursor
    db = MySQLGuildSettingsDb(dir_path=MagicMock(), mydb=mydb)
    # A prior get_cached() found no row and cached None.
    db.cached_settings[42] = None

    g = GuildSettings(guild_id=42, guild_name="late join")
    db.commit(g)

    # get_cached must now see the committed row, not the stale None.
    assert db.get_cached(42) is g

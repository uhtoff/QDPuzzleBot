"""M8 phase 1: the per-guild admin/organiser role-id columns round-trip.

Authorisation used to match Discord role *names*, so anyone with Manage Roles
could mint a role called `admin` and self-assign dashboard admin. The tiers now
resolve to role ids stored per guild; these tests cover the storage half — that
the new columns are read back off the row and written by commit(), in the right
column order.
"""

import re
from unittest.mock import MagicMock

from bot.store.mysqldb import MySQLGuildSettingsDb
from bot.store.puzzle_settings import GuildSettings


def _row(**overrides):
    """A `SELECT * FROM guilds` row with every column import_dict reads."""
    row = {
        "id": 3,
        "guild_id": 99,
        "guild_name": "Team",
        "website_url": "",
        "discord_bot_channel": "",
        "discord_bot_emoji": "",
        "discord_use_voice_channels": False,
        "drive_parent_id": "",
        "drive_resources_id": "",
        "admin_role_ids": None,
        "organiser_role_ids": None,
    }
    row.update(overrides)
    return row


def test_role_id_columns_are_read_off_the_row():
    settings = GuildSettings.import_dict(
        _row(admin_role_ids="101,102", organiser_role_ids="205"))
    assert settings.admin_role_ids == "101,102"
    assert settings.organiser_role_ids == "205"


def test_unconfigured_guild_reads_as_empty_not_missing():
    """A pre-migration guild has NULL in both columns — the not-configured state
    the resolver falls back to name matching for. It must not blow up or lose the
    attribute."""
    settings = GuildSettings.import_dict(_row())
    assert not settings.admin_role_ids
    assert not settings.organiser_role_ids


def _commit_and_capture(settings):
    """Run commit() against a stub cursor and return (statement, params)."""
    cursor = MagicMock()
    cursor.lastrowid = 7
    mydb = MagicMock()
    mydb.cursor.return_value = cursor
    MySQLGuildSettingsDb(dir_path=MagicMock(), mydb=mydb).commit(settings)
    stmt, params = cursor.execute.call_args.args
    return stmt, params


def test_commit_writes_both_role_id_columns_on_update():
    settings = GuildSettings.import_dict(
        _row(admin_role_ids="101,102", organiser_role_ids="205"))
    stmt, params = _commit_and_capture(settings)
    assert "admin_role_ids" in stmt and "organiser_role_ids" in stmt
    # Positional, so a transposed pair would silently swap the two tiers — assert
    # each value lands against its own column, not merely that both are present.
    columns = re.findall(r"`(\w+)`\s*=\s*%s", stmt.split("SET", 1)[1].split("WHERE", 1)[0])
    by_column = dict(zip(columns, params))
    assert by_column["admin_role_ids"] == "101,102"
    assert by_column["organiser_role_ids"] == "205"


def test_commit_writes_both_role_id_columns_on_insert():
    settings = GuildSettings.import_dict(
        _row(id=0, admin_role_ids="101", organiser_role_ids="205"))
    stmt, params = _commit_and_capture(settings)
    columns = [c.strip().strip("`") for c in
               stmt.split("(", 1)[1].split(")", 1)[0].split(",")]
    by_column = dict(zip(columns, params))
    assert by_column["admin_role_ids"] == "101"
    assert by_column["organiser_role_ids"] == "205"

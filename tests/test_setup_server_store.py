"""!setup_server's storage: the run-once lock on guilds, and the hub roster role on
hunts. The lock is written ONLY by mark_setup_complete, so no ordinary settings
write (commit) can ever clear it."""
import datetime
import re
from unittest.mock import MagicMock

from bot.store.hunt_data import HuntData
from bot.store.mysqldb import MySQLGuildSettingsDb, MySQLHuntJsonDb
from bot.store.puzzle_settings import GuildSettings


def _stub_db():
    cursor = MagicMock()
    cursor.lastrowid = 5
    mydb = MagicMock()
    mydb.cursor.return_value = cursor
    return mydb, cursor


def test_setup_complete_time_is_read_off_the_row_as_utc():
    stamped = datetime.datetime(2026, 10, 14, 12, 30)
    settings = GuildSettings.import_dict({"id": 3, "guild_id": 99, "setup_complete_time": stamped})
    assert settings.setup_complete_time == stamped.replace(tzinfo=datetime.timezone.utc)


def test_a_guild_never_set_up_reads_as_none():
    settings = GuildSettings.import_dict({"id": 3, "guild_id": 99, "setup_complete_time": None})
    assert settings.setup_complete_time is None


def test_commit_never_writes_the_lock():
    mydb, cursor = _stub_db()
    settings = GuildSettings()
    settings.id = 3
    settings.guild_id = 99
    settings.setup_complete_time = None
    MySQLGuildSettingsDb(mydb=mydb).commit(settings)
    assert "setup_complete_time" not in cursor.execute.call_args.args[0]


def test_mark_setup_complete_stamps_this_row_and_the_cache():
    mydb, cursor = _stub_db()
    db = MySQLGuildSettingsDb(mydb=mydb)
    settings = GuildSettings()
    settings.id = 3          # the row id: distinct from the snowflake so a swap shows
    settings.guild_id = 99
    db.mark_setup_complete(settings)
    stmt, params = cursor.execute.call_args.args
    assert stmt == "UPDATE `guilds` SET `setup_complete_time`=%s WHERE `id`=%s"
    assert params[1] == 3
    # MariaDB DATETIME is naive; import_dict re-attaches UTC on the way back.
    assert params[0].tzinfo is None
    assert settings.setup_complete_time is not None
    assert settings.setup_complete_time.tzinfo is datetime.timezone.utc
    assert db.cached_settings[99] is settings
    cursor.close.assert_called_once()


def test_hunt_commit_writes_roster_role_id():
    mydb, cursor = _stub_db()
    hunt = HuntData(name="H", uid="abc123")
    hunt.roster_role_id = 777
    hunt.role_id = 555       # a neighbouring id column: a swap with it would show
    MySQLHuntJsonDb(mydb).commit(hunt)
    stmt, params = cursor.execute.call_args.args
    columns = re.findall(r"`(\w+)`", stmt.split("VALUES")[0])[1:]  # [0] is the table
    assert params[columns.index("roster_role_id")] == 777
    assert params[columns.index("role_id")] == 555

"""count_for_guild gates the bot leaving a team server after a hub archive."""
from unittest.mock import MagicMock

from bot.store.mysqldb import MySQLHuntJsonDb


def _db(row):
    db = MySQLHuntJsonDb(mydb=MagicMock())
    db._fetch_one = MagicMock(return_value=row)
    return db


def test_counts_every_hunt_row_on_the_guild_row_archived_or_not():
    db = _db({"n": 2})
    assert db.count_for_guild(5) == 2
    sql, params = db._fetch_one.call_args.args
    assert params == (5,)
    assert "guild_id = %s" in sql
    assert "archived" not in sql   # archived rows still pin the guild


def test_no_row_means_zero():
    assert _db(None).count_for_guild(5) == 0

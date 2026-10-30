"""Slice 4 storage, mocked: hunts.invites_sent_time is never written by the
generic HuntData commit (a stale hunt object must not clear it)."""
from unittest.mock import MagicMock

from bot.store.hunt_data import HuntData
from bot.store.mysqldb import MySQLHuntJsonDb


def _stub_db():
    cursor = MagicMock()
    cursor.lastrowid = 5
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    mydb = MagicMock()
    mydb.cursor.return_value = cursor
    mydb.dict_cursor.return_value = cursor
    return mydb, cursor


def test_hunt_commit_never_writes_invites_sent_time():
    mydb, cursor = _stub_db()
    hunt = HuntData()
    hunt.id = 3
    hunt.name = "Big Hunt"
    MySQLHuntJsonDb(mydb=mydb).commit(hunt)
    assert cursor.execute.call_args_list, "commit issued no SQL"
    assert all("invites_sent_time" not in c.args[0] for c in cursor.execute.call_args_list)


def test_import_dict_ignores_the_invites_sent_time_column():
    hunt = HuntData.import_dict({"id": 3, "name": "Big Hunt", "invites_sent_time": None})
    assert not hasattr(hunt, "invites_sent_time")

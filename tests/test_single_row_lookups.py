"""Single-row lookups must ask the database for a single row (M9).

`_fetch_one` calls `fetchone()` then `close()` on an unbuffered dict cursor, and
mysql-connector's `close()` raises "Unread result found" when rows from the last
result set were never read. So a query that CAN return two rows turns every
subsequent lookup on that value into an InternalError that aborts the command's
before-hook -- which reads, from outside, as the bot hanging. That is not
theoretical: `tests/test_get_channel_type.py` exists because of a live incident
of exactly this shape (`!unsolve` on the 'Supergirl' metapuzzle).

Half the DAOs already guard with `ORDER BY id ASC LIMIT 1`. Four did not, and all
four filter on columns carrying no UNIQUE constraint, so one duplicate row is
enough to wedge them.

The fake cursor below honours `LIMIT 1` the way the server would. That is what
makes these tests falsifiable: without the clause in the SQL, two rows are
staged, one is read, and `close()` raises exactly as production does.
"""
from unittest.mock import MagicMock, patch

import pytest

from bot.store.mysqldb import (
    MySQLGuildSettingsDb,
    MySQLHuntJsonDb,
    MySQLPuzzleJsonDb,
)


class LimitAwareCursor:
    """A mysql.connector-style unbuffered cursor that respects LIMIT 1.

    Staging happens in execute(), not __init__, so the statement decides how many
    rows come back -- the whole point of the test.
    """

    def __init__(self, rows):
        self._all = list(rows)
        self._rows = []
        self.statements = []

    def execute(self, stmt, data=()):
        self.statements.append(stmt)
        self._rows = self._all[:1] if "LIMIT 1" in stmt.upper() else list(self._all)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def close(self):
        if self._rows:
            raise RuntimeError("Unread result found")


def _db(cls, rows):
    cursor = LimitAwareCursor(rows)
    mydb = MagicMock()
    mydb.dict_cursor.return_value = cursor
    return cls(mydb=mydb), cursor


_TWO = [{"id": 1, "name": "first"}, {"id": 2, "name": "second"}]


def test_hunt_get_by_attr_survives_a_duplicate_name():
    db, cursor = _db(MySQLHuntJsonDb, _TWO)
    with patch("bot.store.mysqldb.HuntData") as hunt_data:
        hunt_data.import_dict.side_effect = lambda row: row
        result = db.get_by_attr(name="Mystery Hunt")

    assert result == _TWO[0], "must return the first row deterministically"
    assert "LIMIT 1" in cursor.statements[0].upper()


def test_puzzle_get_survives_a_duplicate_channel_id():
    db, cursor = _db(MySQLPuzzleJsonDb, _TWO)
    with patch.object(MySQLPuzzleJsonDb, "construct_puzzle", lambda self, row: row):
        result = db.get(1, 999, None, None)

    assert result == _TWO[0]
    assert "LIMIT 1" in cursor.statements[0].upper()


def test_puzzle_get_by_attr_survives_a_duplicate_value():
    db, cursor = _db(MySQLPuzzleJsonDb, _TWO)
    with patch.object(MySQLPuzzleJsonDb, "construct_puzzle", lambda self, row: row):
        result = db.get_by_attr(google_page_id="P1")

    assert result == _TWO[0]
    assert "LIMIT 1" in cursor.statements[0].upper()


def test_guild_settings_get_survives_a_duplicate_guild_row():
    db, cursor = _db(MySQLGuildSettingsDb, _TWO)
    with patch("bot.store.mysqldb.GuildSettings") as settings:
        settings.import_dict.side_effect = lambda row: row
        result = db.get(4242)

    assert result == _TWO[0]
    assert "LIMIT 1" in cursor.statements[0].upper()


@pytest.mark.parametrize("stmt, expected_rows", [
    ("SELECT * FROM t WHERE x = %s", 2),
    ("SELECT * FROM t WHERE x = %s ORDER BY `id` ASC LIMIT 1", 1),
])
def test_the_harness_itself_distinguishes_the_two_statements(stmt, expected_rows):
    """Guard the guard: if LIMIT 1 stopped changing what the fake returns, every
    test above would pass vacuously."""
    cursor = LimitAwareCursor(_TWO)
    cursor.execute(stmt)
    assert len(cursor.fetchall()) == expected_rows

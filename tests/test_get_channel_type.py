"""Regression tests for MySQLGuildSettingsDb.get_channel_type.

A puzzle promoted to a metapuzzle reuses its puzzle channel as the group's
category_id, so the get_channel_type UNION returns two rows ('Puzzle' and
'Group') for that one channel id. The reader must consume the whole result set:
leaving rows unread makes mysql.connector's cursor.close() raise
"Unread result found", which previously aborted the command before-hook and made
the bot look like it had hung (live: !unsolve on the 'Supergirl' metapuzzle).
"""

from unittest.mock import MagicMock

from bot.store.mysqldb import MySQLGuildSettingsDb


class FakeCursor:
    """Minimal mysql.connector-style unbuffered cursor.

    close() raises like the real driver when rows from the last result set were
    never read, so a fetchone()-and-close() caller fails exactly as in production.
    """

    def __init__(self, rows):
        self._rows = list(rows)

    def execute(self, stmt, data):
        # Result set is already staged in __init__; nothing to do.
        pass

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def close(self):
        if self._rows:
            raise RuntimeError("Unread result found")


def _db_with_rows(rows):
    mydb = MagicMock()
    mydb.dict_cursor.return_value = FakeCursor(rows)
    return MySQLGuildSettingsDb(dir_path=MagicMock(), mydb=mydb)


def test_dual_role_channel_does_not_crash_and_prefers_puzzle():
    # Order mirrors the UNION: the 'Group' (category_id) row precedes the
    # 'Puzzle' (channel_id) row, so fetchone() alone would pick 'Group' and then
    # crash on close() with an unread 'Puzzle' row.
    db = _db_with_rows(
        [
            {"channel_id": 1520030323503792211, "channel_type": "Group"},
            {"channel_id": 1520030323503792211, "channel_type": "Puzzle"},
        ]
    )
    assert db.get_channel_type(1520030323503792211) == "Puzzle"


def test_single_puzzle_row():
    db = _db_with_rows([{"channel_id": 111, "channel_type": "Puzzle"}])
    assert db.get_channel_type(111) == "Puzzle"


def test_single_hunt_row():
    db = _db_with_rows([{"channel_id": 222, "channel_type": "Hunt"}])
    assert db.get_channel_type(222) == "Hunt"


def test_no_match_returns_none():
    db = _db_with_rows([])
    assert db.get_channel_type(999) is None


def test_puzzle_outranks_hunt():
    db = _db_with_rows(
        [
            {"channel_id": 333, "channel_type": "Hunt"},
            {"channel_id": 333, "channel_type": "Puzzle"},
        ]
    )
    assert db.get_channel_type(333) == "Puzzle"

"""The slot writers must not use INSERT ... SELECT on puzzles: under InnoDB
REPEATABLE READ that takes shared next-key locks on every puzzles row it scans
(no index on channel_id / google_sheet_id), once per human message, blocking
concurrent solves and creates. A plain SELECT is a lock-free consistent read."""
import datetime

from bot.store.mysqldb import MySQLPuzzleActivityDb

AT = datetime.datetime(2026, 11, 1, 12, 7)


class _Cursor:
    def __init__(self, log, ids):
        self.log, self.ids = log, ids

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return [{"id": i} for i in self.ids]

    def fetchone(self):
        return None

    def close(self):
        pass


class _Db:
    def __init__(self, ids):
        self.log, self.ids = [], ids

    def dict_cursor(self):
        return _Cursor(self.log, self.ids)


def _check(log, column, ids, update):
    selects = [s for s, _ in log if s.startswith("SELECT")]
    inserts = [(s, p) for s, p in log if s.startswith("INSERT")]
    assert all("SELECT" not in s for s, _ in inserts), "INSERT ... SELECT locks puzzles rows"
    assert len(selects) == 1 and f"WHERE {column} = %s" in selects[0]
    assert [p[0] for _, p in inserts] == ids
    assert all(p[1] == datetime.datetime(2026, 11, 1, 12, 5) for _, p in inserts)
    assert all(update in s for s, _ in inserts)


def test_add_chat_reads_then_inserts_values():
    db = _Db([5, 6])
    MySQLPuzzleActivityDb(mydb=db).add_chat(1001, AT)
    _check(db.log, "channel_id", [5, 6], "chat_count = chat_count + 1")


def test_mark_sheet_edited_reads_then_inserts_values():
    db = _Db([7])
    MySQLPuzzleActivityDb(mydb=db).mark_sheet_edited("S1", AT)
    _check(db.log, "google_sheet_id", [7], "sheet_edited = 1")


def test_no_matching_puzzle_writes_nothing():
    db = _Db([])
    MySQLPuzzleActivityDb(mydb=db).add_chat(424242, AT)
    assert [s for s, _ in db.log if s.startswith("INSERT")] == []

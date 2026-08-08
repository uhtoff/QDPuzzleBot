"""T5 (docs/reviews/2026-08-07-comprehensive-review.md): batch puzzle hydration.

construct_puzzle ran four queries per row (tags, additional_sheets, is_target,
is_minimeta), and every list path looped it -- ~400 sequential round-trips for a
100-puzzle hunt, synchronously, on the gateway thread. The list paths now go
through construct_puzzles, which fans each of the four into one IN (...) query.

The attribution tests deliberately use TWO puzzles: with one, a hydrator that
dumped every row onto every puzzle would still pass.

Locally-named helpers -- the parallel runner shards by file.
"""
from unittest.mock import MagicMock

from bot.store.mysqldb import MySQLPuzzleJsonDb


class _BatchCursor:
    """Routes each SELECT to canned rows and records the SQL that was run."""

    def __init__(self, log, tables):
        self._log = log
        self._tables = tables
        self._rows = []

    def execute(self, sql, params=()):
        self._log.append((sql, params))
        for table, rows in self._tables.items():
            # Match the FROM clause, not the bare name: "tags.tag_group_id"
            # contains "tag_group" and would route the tags query to tag_group.
            if f"FROM {table}" in sql:
                self._rows = rows(params) if callable(rows) else rows
                return
        self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


def _batch_db(**tables):
    """A MySQLPuzzleJsonDb over a stub connection, plus the query log."""
    log = []
    mydb = MagicMock()
    mydb.dict_cursor.side_effect = lambda: _BatchCursor(log, tables)
    mydb.cursor.side_effect = lambda *a, **k: _BatchCursor(log, tables)
    return MySQLPuzzleJsonDb(mydb=mydb), log


def _batch_row(pid, name="P"):
    return {"id": pid, "name": name, "channel_id": 1000 + pid, "hunt_id": 7}


def test_t5_query_count_does_not_grow_with_the_batch():
    """The whole point: 4 queries for 1 puzzle, still 4 for many."""
    db_one, log_one = _batch_db()
    db_one.construct_puzzles([_batch_row(1)])

    db_many, log_many = _batch_db()
    db_many.construct_puzzles([_batch_row(i) for i in range(1, 7)])

    assert len(log_one) == len(log_many) == 4, (
        f"expected a fixed 4 hydration queries, got {len(log_one)} and {len(log_many)}"
    )


def test_t5_no_queries_for_an_empty_batch():
    db, log = _batch_db()
    assert db.construct_puzzles([]) == []
    assert log == []


def test_t5_tags_land_on_the_right_puzzle():
    db, _ = _batch_db(tags=[
        {"puzzle_id": 1, "tag_group_id": 10, "edge_type": "feeds"},
        {"puzzle_id": 2, "tag_group_id": 20, "edge_type": "informs"},
    ])
    one, two = db.construct_puzzles([_batch_row(1), _batch_row(2)])

    assert one.tags == [10]
    assert two.tags == [20]
    assert one.tag_edges == {10: {"feeds"}}
    assert two.tag_edges == {20: {"informs"}}


def test_t5_additional_sheets_land_on_the_right_puzzle():
    db, _ = _batch_db(additional_sheets=[
        {"puzzle_id": 1, "id": 100, "puzzle_name": "tab-one"},
        {"puzzle_id": 2, "id": 200, "puzzle_name": "tab-two"},
    ])
    one, two = db.construct_puzzles([_batch_row(1), _batch_row(2)])

    assert [s.puzzle_name for s in one.additional_sheets] == ["tab-one"]
    assert [s.puzzle_name for s in two.additional_sheets] == ["tab-two"]


def test_t5_is_target_is_per_puzzle():
    db, _ = _batch_db(tag_group=[{"target_puzzle_id": 2}])
    one, two = db.construct_puzzles([_batch_row(1), _batch_row(2)])

    assert one.is_target is False
    assert two.is_target is True


def test_t5_is_minimeta_is_per_puzzle():
    db, _ = _batch_db(puzzles=[{"parent_puzzle_id": 1}])
    one, two = db.construct_puzzles([_batch_row(1), _batch_row(2)])

    assert one.is_minimeta is True
    assert two.is_minimeta is False


def test_t5_row_order_is_preserved():
    """get_children relies on the SQL's ORDER BY surviving hydration."""
    db, _ = _batch_db()
    rows = [_batch_row(3, "c"), _batch_row(1, "a"), _batch_row(2, "b")]
    assert [p.id for p in db.construct_puzzles(rows)] == [3, 1, 2]


def test_t5_single_row_path_still_hydrates():
    """construct_puzzle now delegates to the batch builder."""
    db, _ = _batch_db(
        tags=[{"puzzle_id": 5, "tag_group_id": 50, "edge_type": "feeds"}],
        tag_group=[{"target_puzzle_id": 5}],
    )
    puzzle = db.construct_puzzle(_batch_row(5))

    assert puzzle.id == 5
    assert puzzle.tags == [50]
    assert puzzle.is_target is True

"""T2: an id-addressed UPDATE that matches no row is logged, not silently dropped.

LIVE MySQL (puzzlebot_test): the behaviour under test is cursor.rowcount from a real
UPDATE, which a mocked cursor would simply assert back at us.
"""

import logging

from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


def test_commit_to_a_deleted_row_logs_an_error(caplog):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (live_hunt_id(), "MissingRowTest"),
    )
    puzzle_id = cur.lastrowid
    cur.close()

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    assert puzzle is not None

    # The row goes away underneath the object we are holding -- exactly what a
    # concurrent delete_round does to a bang command's snapshot.
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()

    puzzle.status = "Critical"
    with caplog.at_level(logging.ERROR, logger="bot.store.mysqldb"):
        PuzzleJsonDb.commit(puzzle)

    assert any(
        "puzzles" in r.message and str(puzzle_id) in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ), f"no ERROR log naming the table and id; got {[r.message for r in caplog.records]}"


def test_commit_to_a_live_row_logs_nothing(caplog):
    """The guard must not cry wolf on the normal path."""
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (live_hunt_id(), "LiveRowTest"),
    )
    puzzle_id = cur.lastrowid
    cur.close()
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        assert puzzle is not None
        puzzle.status = "Critical"

        with caplog.at_level(logging.ERROR, logger="bot.store.mysqldb"):
            PuzzleJsonDb.commit(puzzle)

        assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
        cur.close()

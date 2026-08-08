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


def test_a_commit_that_changes_no_column_logs_nothing(caplog):
    """The false-positive case, and the common one.

    rowcount == 0 does NOT mean "no row matched" -- MariaDB reports 0 for an UPDATE
    that matched a row and changed nothing. Committing an unchanged object is the
    simplest instance; the one that matters in production is an edges-only commit
    (see below), because tags/tag_edges are in SPECIAL_ATTR and never appear in the
    generic UPDATE at all.

    Deliberately does NOT set a field first: the sibling test above does that, and a
    changed column is precisely the case that cannot misfire.
    """
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (live_hunt_id(), "UnchangedRowTest"),
    )
    puzzle_id = cur.lastrowid
    cur.close()
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        assert puzzle is not None
        # Normalise the row FIRST. A freshly-INSERTed row has NULLs where PuzzleData
        # defaults to "", so the first commit genuinely changes columns and returns
        # rowcount=1 -- which would make this test pass for the wrong reason.
        PuzzleJsonDb.commit(puzzle)

        with caplog.at_level(logging.ERROR, logger="bot.store.mysqldb"):
            PuzzleJsonDb.commit(puzzle)   # now nothing changes at all

        assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == [], (
            "committing an unchanged live row logged a spurious 'write was dropped'"
        )
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
        cur.close()


def test_an_edges_only_commit_logs_nothing(caplog):
    """The production shape of the false positive: PuzzleService.tag_with_edge does
    exactly this (add_edge then commit) on every retag, every evidence link and
    every group conversion. tags/tag_edges are in SPECIAL_ATTR, so no scalar column
    changes and rowcount is always 0 -- logging on that alone would fire an ERROR
    claiming data loss on every successful !feeds."""
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (live_hunt_id(), "EdgesOnlyTest"),
    )
    puzzle_id = cur.lastrowid
    cur.execute("INSERT INTO tag_group (hunt_id) VALUES (%s)", (live_hunt_id(),))
    group_id = cur.lastrowid
    cur.close()
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        assert puzzle is not None
        # Normalise the row first (see the sibling test): otherwise the INSERT's
        # NULL columns get written on this commit and rowcount is 1, not 0, and the
        # false positive under test never arises.
        PuzzleJsonDb.commit(puzzle)

        puzzle.add_edge(group_id, "feeds")   # membership only -- no scalar column

        with caplog.at_level(logging.ERROR, logger="bot.store.mysqldb"):
            PuzzleJsonDb.commit(puzzle)

        assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == [], (
            "an edges-only commit logged a spurious 'write was dropped'"
        )
        # And the edge really did persist -- proving the commit was a real write
        # that simply changed no scalar column, not a no-op we mislabelled as fine.
        fresh = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        assert fresh is not None and fresh.has_edge(group_id, "feeds")
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
        cur.execute("DELETE FROM tag_group WHERE id = %s", (group_id,))
        cur.close()

"""T1: set_positions writes a whole ordering in one statement.

LIVE MySQL (puzzlebot_test) -- follows tests/test_tag_edges.py.

THREE rows, scrambled. Order is a relationship between items: with one or two rows a
transposition or a truncation is indistinguishable from success, so a smaller fixture
cannot fail against a wrong implementation.
"""

import datetime

from bot.store import PuzzleImageJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_puzzle(name):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (live_hunt_id(), name)
    )
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _insert_media():
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO media (hunt_id, path, filename, content_type, size, origin,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (live_hunt_id(), "uploads/positions/x.png", "x.png", "image/png", 100,
         "upload", datetime.datetime(2020, 1, 1)),
    )
    mid = cur.lastrowid
    cur.close()
    mydb.commit()
    return mid


def _insert_image(puzzle_id, position):
    media_id = _insert_media()
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzle_images (puzzle_id, media_id, position, created_at)"
        " VALUES (%s, %s, %s, %s)",
        (puzzle_id, media_id, position, datetime.datetime(2020, 1, 1)),
    )
    iid = cur.lastrowid
    cur.close()
    mydb.commit()
    return iid


def _positions(image_ids):
    cur = mydb.cursor()
    placeholders = ",".join(["%s"] * len(image_ids))
    cur.execute(
        f"SELECT id, position FROM puzzle_images WHERE id IN ({placeholders})",
        tuple(image_ids),
    )
    got = {r[0]: r[1] for r in cur.fetchall()}
    cur.close()
    return got


def _cleanup(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzle_images WHERE puzzle_id = %s", (puzzle_id,))
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()
    mydb.commit()


def test_set_positions_writes_the_exact_scrambled_order():
    pid = _insert_puzzle("PositionTest")
    try:
        a = _insert_image(pid, 0)
        b = _insert_image(pid, 1)
        c = _insert_image(pid, 2)

        # Reverse-ish: a genuine permutation of three, so neither a no-op nor a
        # simple swap of the first two would satisfy it.
        PuzzleImageJsonDb.set_positions([c, a, b])

        assert _positions([a, b, c]) == {c: 0, a: 1, b: 2}
    finally:
        _cleanup(pid)


def test_set_positions_with_an_empty_list_is_a_no_op():
    """An empty ordering must not build a malformed CASE/IN statement."""
    PuzzleImageJsonDb.set_positions([])


def test_set_positions_does_not_touch_other_puzzles_images():
    mine = _insert_puzzle("PositionMine")
    theirs = _insert_puzzle("PositionTheirs")
    try:
        a = _insert_image(mine, 0)
        b = _insert_image(mine, 1)
        other = _insert_image(theirs, 7)

        PuzzleImageJsonDb.set_positions([b, a])

        assert _positions([other]) == {other: 7}
    finally:
        _cleanup(mine)
        _cleanup(theirs)

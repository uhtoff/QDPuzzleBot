"""T1: bulk_update_edge_type inserts the new edges before deleting the old ones.

LIVE MySQL (puzzlebot_test) -- follows tests/test_tag_edges.py. The behaviour under
test is the ORDER of two SQL statements against a real UNIQUE constraint, which a
mock cannot reproduce.

The fixture deliberately has TWO members, one of which ALREADY holds the target edge
type. A single-member fixture, or two members neither of which holds the target edge,
passes against the old DELETE-then-INSERT code and so proves nothing.
"""

from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_tag_group():
    cur = mydb.cursor()
    cur.execute("INSERT INTO tag_group (hunt_id) VALUES (%s)", (live_hunt_id(),))
    gid = cur.lastrowid
    cur.close()
    return gid


def _insert_puzzle(name):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (live_hunt_id(), name)
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _insert_edge(tag_group_id, puzzle_id, edge_type):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
        (tag_group_id, puzzle_id, edge_type),
    )
    cur.close()


def _edges(tag_group_id, puzzle_id):
    cur = mydb.cursor()
    cur.execute(
        "SELECT edge_type FROM tags WHERE tag_group_id=%s AND puzzle_id=%s",
        (tag_group_id, puzzle_id),
    )
    edges = sorted(r[0] for r in cur.fetchall())
    cur.close()
    return edges


def _cleanup(group_id, puzzle_ids):
    cur = mydb.cursor()
    for pid in puzzle_ids:
        cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.execute("DELETE FROM tag_group WHERE id = %s", (group_id,))
    cur.close()


def test_conversion_keeps_every_member_and_dedupes_the_pre_existing_edge():
    gid = _insert_tag_group()
    feeder = _insert_puzzle("BulkFeeder")
    already = _insert_puzzle("BulkAlready")
    try:
        _insert_edge(gid, feeder, "feeds")
        # This member already holds the edge we are converting TO, alongside an old
        # one. It is the case that makes a naive single-UPDATE violate the UNIQUE
        # constraint, and the case the delete's edge_type<>new_edge clause protects.
        _insert_edge(gid, already, "feeds")
        _insert_edge(gid, already, "informs")

        PuzzleJsonDb.bulk_update_edge_type(gid, "informs")

        # Nobody lost their membership...
        assert _edges(gid, feeder) == ["informs"]
        # ...and the member that already had the target edge holds it exactly once.
        assert _edges(gid, already) == ["informs"]
    finally:
        _cleanup(gid, [feeder, already])


def test_conversion_leaves_the_target_self_edge_untouched():
    """bulk_re_edge re-pins the 'target' self-edge itself; this method must not
    touch it, or a converted group loses its target."""
    gid = _insert_tag_group()
    target = _insert_puzzle("BulkTarget")
    try:
        _insert_edge(gid, target, "target")

        PuzzleJsonDb.bulk_update_edge_type(gid, "informs")

        assert _edges(gid, target) == ["target"]
    finally:
        _cleanup(gid, [target])

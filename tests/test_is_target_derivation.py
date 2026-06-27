"""B2: PuzzleData.is_target is derived (eager-loaded) from tag_group.target_puzzle_id.

LIVE MySQL (puzzlebot_test), same convention as test_tag_edges.py: insert
FK-valid rows, assert through the store load path, tear down in finally.

A is-target puzzle is, structurally, just "some tag_group targets it" — so the
fed-meta, capstone, and runaround cases from the spec all reduce to the single
positive case here; a bare puzzle and a metaless/converted Group (no target) both
reduce to the negative case.
"""
from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_puzzle(hunt_id=None, name="HasTargetTest"):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (hunt_id or live_hunt_id(), name),
    )
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _insert_tag_group(hunt_id=None, target_puzzle_id=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tag_group (hunt_id, target_puzzle_id) VALUES (%s, %s)",
        (hunt_id or live_hunt_id(), target_puzzle_id),
    )
    rid = cur.lastrowid
    cur.close()
    mydb.commit()
    return rid


def _delete_puzzle(pid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.close()
    mydb.commit()


def _delete_tag_group(rid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM tag_group WHERE id = %s", (rid,))
    cur.close()
    mydb.commit()


def test_is_target_true_when_a_tag_group_targets_the_puzzle():
    pid = _insert_puzzle(name="DerivedMeta")
    rid = _insert_tag_group(target_puzzle_id=pid)
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle.is_target is True
    finally:
        _delete_tag_group(rid)
        _delete_puzzle(pid)


def test_is_target_false_for_bare_puzzle():
    pid = _insert_puzzle(name="BarePuzzle")
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle.is_target is False
    finally:
        _delete_puzzle(pid)


def test_is_target_false_for_metaless_group_no_target():
    # A metaless/converted Group has no target_puzzle_id → not is-target.
    pid = _insert_puzzle(name="GroupMember")
    rid = _insert_tag_group(target_puzzle_id=None)
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=pid)
        assert puzzle.is_target is False
    finally:
        _delete_tag_group(rid)
        _delete_puzzle(pid)

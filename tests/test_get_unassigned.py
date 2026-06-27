"""get_unassigned: feeds-only membership for the Unassigned Feeders tab (Milestone D, Task 5).

LIVE MySQL (puzzlebot_test), same convention as test_tag_edges.py /
test_is_target_derivation.py: insert FK-valid rows, assert through the store load
path, tear down in a finally block. Helpers are file-local (uniquely prefixed
`_gu_`) so the parallel gate never resolves a cross-file helper.

New rule under test: a puzzle is an *unassigned feeder* iff it has NO `feeds` edge
to a meta (a tag_group with a target). `informs` / `none` membership no longer
disqualifies. Target puzzles (is_target) and mini-meta feeders (parent_puzzle_id)
stay excluded.

This test FAILS before the Task 5 query change: today's membership clause excludes
any puzzle tagged into a target-bearing group regardless of edge_type, so the
informs-only and none-only members are wrongly dropped.

Looks up a real hunt id at runtime (FK to hunts) like the sibling live-DB tests,
and asserts membership per-puzzle (subset, not exact set) so any other rows on that
hunt in puzzlebot_test don't make the assertions brittle.
"""
from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id

HUNT = live_hunt_id()  # a real hunts row (tag_group.hunt_id FKs hunts(id)); same as sibling tests


def _gu_insert_puzzle(name, parent_puzzle_id=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name, parent_puzzle_id) VALUES (%s, %s, %s)",
        (HUNT, name, parent_puzzle_id),
    )
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _gu_insert_tag_group(target_puzzle_id=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tag_group (hunt_id, target_puzzle_id) VALUES (%s, %s)",
        (HUNT, target_puzzle_id),
    )
    rid = cur.lastrowid
    cur.close()
    mydb.commit()
    return rid


def _gu_insert_tag(tag_group_id, puzzle_id, edge_type):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s)",
        (tag_group_id, puzzle_id, edge_type),
    )
    cur.close()
    mydb.commit()


def _gu_delete_tag_group(rid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM tag_group WHERE id = %s", (rid,))  # cascades tags
    cur.close()
    mydb.commit()


def _gu_delete_puzzle(pid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.close()
    mydb.commit()


def test_get_unassigned_is_feeds_only():
    # Meta target puzzle + the group that targets it (makes target is_target).
    target_pid = _gu_insert_puzzle("MetaTarget")
    meta_rid = _gu_insert_tag_group(target_puzzle_id=target_pid)

    feeder_pid = _gu_insert_puzzle("Feeder")      # feeds the meta -> EXCLUDED
    informer_pid = _gu_insert_puzzle("Informer")  # only informs   -> INCLUDED
    grouped_pid = _gu_insert_puzzle("Grouped")    # only none       -> INCLUDED
    untagged_pid = _gu_insert_puzzle("Untagged")  # no tags         -> INCLUDED
    child_pid = _gu_insert_puzzle("MiniChild", parent_puzzle_id=target_pid)  # EXCLUDED

    try:
        _gu_insert_tag(meta_rid, feeder_pid, "feeds")
        _gu_insert_tag(meta_rid, informer_pid, "informs")
        _gu_insert_tag(meta_rid, grouped_pid, "none")

        result_ids = {p.id for p in PuzzleJsonDb.get_unassigned(HUNT)}

        # Included: answer feeds nothing (informs / none / untagged).
        assert informer_pid in result_ids       # only informs -> still unassigned feeder
        assert grouped_pid in result_ids        # only none    -> still unassigned feeder
        assert untagged_pid in result_ids       # no tags
        # Excluded:
        assert feeder_pid not in result_ids      # a feeds edge to a meta disqualifies
        assert target_pid not in result_ids      # is_target excluded
        assert child_pid not in result_ids       # mini-meta feeder excluded
    finally:
        _gu_delete_tag_group(meta_rid)
        # child references target via parent_puzzle_id -> delete child first
        for pid in (child_pid, feeder_pid, informer_pid, grouped_pid,
                    untagged_pid, target_pid):
            _gu_delete_puzzle(pid)

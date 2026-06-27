"""M7: tags.edge_type is live on read + a tag_puzzle_with_edge upsert writer.

LIVE MySQL (puzzlebot_test). Follows the live-DB convention used by
test_backfill_consistency.py (`from bot.store import mydb`); these store-layer
behaviours are not mockable. Each test inserts its own tag_group/puzzle/tag rows
(FK-valid) and tears them down in a finally block. The tags table FKs cascade on
puzzle/tag_group delete, so deleting the parents clears the tag rows.

The upsert test relies on migration 005's UNIQUE KEY (tag_group_id, puzzle_id); if
005 is not yet applied the writer simply does not exist (Phase 1) — the test then
fails on the missing seam, which is the right reason.
"""

from bot.store import PuzzleJsonDb, mydb
from bot.store.puzzle_data import PuzzleData
from tests.conftest import live_hunt_id


def _insert_tag_group(hunt_id=None):
    cur = mydb.cursor()
    cur.execute("INSERT INTO tag_group (hunt_id) VALUES (%s)", (hunt_id or live_hunt_id(),))
    rid = cur.lastrowid
    cur.close()
    mydb.commit()
    return rid


def _insert_puzzle(hunt_id=None, name="EdgeTest"):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (hunt_id or live_hunt_id(), name),
    )
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _insert_tag(tag_group_id, puzzle_id, edge_type=None):
    cur = mydb.cursor()
    if edge_type is None:
        cur.execute(
            "INSERT INTO tags (tag_group_id, puzzle_id) VALUES (%s, %s)",
            (tag_group_id, puzzle_id),
        )
    else:
        cur.execute(
            "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s)",
            (tag_group_id, puzzle_id, edge_type),
        )
    cur.close()
    mydb.commit()


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()
    mydb.commit()


def _delete_tag_group(tag_group_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM tag_group WHERE id = %s", (tag_group_id,))
    cur.close()
    mydb.commit()


def _tag_rows(tag_group_id, puzzle_id):
    cur = mydb.cursor(dictionary=True)
    cur.execute(
        "SELECT edge_type FROM tags WHERE tag_group_id = %s AND puzzle_id = %s",
        (tag_group_id, puzzle_id),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def test_get_tags_populates_tag_edges():
    rid_default = _insert_tag_group()
    rid_aggr = _insert_tag_group()
    pid = _insert_puzzle()
    try:
        _insert_tag(rid_default, pid)                       # defaults to feeds
        _insert_tag(rid_aggr, pid, edge_type="none")

        puzzle = PuzzleData()
        puzzle.id = pid
        PuzzleJsonDb.get_tags(puzzle)

        # tags is unchanged: still the list of tag_group_ids.
        assert set(puzzle.tags) == {rid_default, rid_aggr}
        # tag_edges maps each tag_group_id to its edge_type.
        assert puzzle.tag_edges[rid_default] == "feeds"
        assert puzzle.tag_edges[rid_aggr] == "none"
    finally:
        _delete_puzzle(pid)
        _delete_tag_group(rid_default)
        _delete_tag_group(rid_aggr)


def test_tag_puzzle_with_edge_inserts_then_updates():
    rid = _insert_tag_group()
    pid = _insert_puzzle()
    try:
        PuzzleJsonDb.tag_puzzle_with_edge(pid, rid, "none")
        PuzzleJsonDb.tag_puzzle_with_edge(pid, rid, "feeds")  # update in place

        rows = _tag_rows(rid, pid)
        assert len(rows) == 1                       # one edge per pair, not duplicated
        assert rows[0]["edge_type"] == "feeds"  # latest value won
    finally:
        _delete_puzzle(pid)
        _delete_tag_group(rid)

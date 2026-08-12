"""Feeder pool FK cascades (docs/superpowers/specs/2026-08-12-feeder-pool-design.md).

LIVE MySQL (puzzlebot_test), following the convention in test_tag_edges.py: FK
cascade behaviour is not mockable, so these tests insert real puzzles/pool
items/claims and tear them down in a finally block. Deleting a puzzle cascades
through feeder_pool_items (as its minimeta) and feeder_claims (as its item's
target), per migration 043.
"""
from mysql.connector.errors import IntegrityError

from bot.store import mydb
from tests.conftest import live_hunt_id


def _insert_puzzle(hunt_id=None, name="FeederPoolCascadeTest"):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (hunt_id or live_hunt_id(), name),
    )
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()
    mydb.commit()


def _insert_pool_item(minimeta_id, text, position=0):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO feeder_pool_items (minimeta_puzzle_id, text, position)"
        " VALUES (%s, %s, %s)",
        (minimeta_id, text, position),
    )
    item_id = cur.lastrowid
    cur.close()
    mydb.commit()
    return item_id


def _insert_claim(item_id, target_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO feeder_claims (item_id, target_id, confirmed) VALUES (%s, %s, 0)",
        (item_id, target_id),
    )
    cur.close()
    mydb.commit()


def _pool_item_count(item_id):
    cur = mydb.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS n FROM feeder_pool_items WHERE id = %s", (item_id,))
    n = cur.fetchone()["n"]
    cur.close()
    return n


def _claim_targets(item_id):
    cur = mydb.cursor(dictionary=True)
    cur.execute("SELECT target_id FROM feeder_claims WHERE item_id = %s ORDER BY target_id", (item_id,))
    rows = cur.fetchall()
    cur.close()
    return [r["target_id"] for r in rows]


def test_deleting_the_minimeta_cascades_pool_and_claims():
    minimeta_id = _insert_puzzle(name="cascade-minimeta")
    mini_id = _insert_puzzle(name="cascade-mini")
    try:
        item_id = _insert_pool_item(minimeta_id, "ALBATROSS")
        _insert_claim(item_id, mini_id)

        _delete_puzzle(minimeta_id)
        minimeta_id = None

        assert _pool_item_count(item_id) == 0, "pool item survived its minimeta"
        assert _claim_targets(item_id) == [], "claim survived its pool item"
    finally:
        if minimeta_id is not None:
            _delete_puzzle(minimeta_id)
        _delete_puzzle(mini_id)


def test_deleting_a_mini_cascades_only_its_own_claims():
    """Two minis, two claims -- so a cascade that deleted everything, or deleted
    by the wrong key, is distinguishable from one that deleted the right row."""
    minimeta_id = _insert_puzzle(name="cascade-minimeta-2")
    mini_a = _insert_puzzle(name="cascade-mini-a")
    mini_b = _insert_puzzle(name="cascade-mini-b")
    try:
        item_id = _insert_pool_item(minimeta_id, "MARZIPAN")
        _insert_claim(item_id, mini_a)
        _insert_claim(item_id, mini_b)

        _delete_puzzle(mini_a)
        mini_a = None

        assert _claim_targets(item_id) == [mini_b], "expected only mini_b's claim to survive"
        assert _pool_item_count(item_id) == 1, "the pool item should outlive one claimant"
    finally:
        _delete_puzzle(minimeta_id)
        if mini_a is not None:
            _delete_puzzle(mini_a)
        _delete_puzzle(mini_b)


def test_the_same_target_cannot_claim_one_item_twice():
    minimeta_id = _insert_puzzle(name="cascade-minimeta-3")
    mini_id = _insert_puzzle(name="cascade-mini-c")
    try:
        item_id = _insert_pool_item(minimeta_id, "QUAY")
        _insert_claim(item_id, mini_id)

        try:
            _insert_claim(item_id, mini_id)
            raise AssertionError("duplicate claim was accepted")
        except IntegrityError:
            pass
    finally:
        _delete_puzzle(minimeta_id)
        _delete_puzzle(mini_id)

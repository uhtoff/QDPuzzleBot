"""T3: deleting a hunt cascades to its hunt_category rows (migration 038).

LIVE MySQL (puzzlebot_test) -- follows the live-DB convention of
tests/test_tag_edges.py. The cascade is a database guarantee, so it cannot be
mocked: the point is that the DATABASE removes the rows on a plain hunt delete,
which is what makes the guild-less (web/API) delete path safe. The app-side
cleanup it replaces only runs when a Discord guild is in hand.
"""

from bot.store import mydb


def _insert_hunt(name):
    cur = mydb.cursor()
    cur.execute("INSERT INTO hunts (name) VALUES (%s)", (name,))
    hunt_id = cur.lastrowid
    cur.close()
    return hunt_id


def _insert_hunt_category(hunt_id, category_id, role="unsolved", seq=0):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO hunt_category (hunt_id, category_id, role, seq) "
        "VALUES (%s, %s, %s, %s)",
        (hunt_id, category_id, role, seq),
    )
    cur.close()


def _category_count(hunt_id):
    cur = mydb.cursor()
    cur.execute("SELECT COUNT(*) FROM hunt_category WHERE hunt_id = %s", (hunt_id,))
    n = cur.fetchone()[0]
    cur.close()
    return n


def _delete_hunt(hunt_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM hunts WHERE id = %s", (hunt_id,))
    cur.close()


def test_deleting_a_hunt_removes_its_category_rows():
    hunt_id = _insert_hunt("CascadeHunt")
    try:
        # TWO rows: one cannot prove the delete removed "all" of them, and the
        # real layout keeps several (unsolved + solved, seq 0,1,2...).
        _insert_hunt_category(hunt_id, 880000000000000001, "unsolved", 0)
        _insert_hunt_category(hunt_id, 880000000000000002, "solved", 0)
        assert _category_count(hunt_id) == 2

        _delete_hunt(hunt_id)

        assert _category_count(hunt_id) == 0
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM hunt_category WHERE hunt_id = %s", (hunt_id,))
        cur.execute("DELETE FROM hunts WHERE id = %s", (hunt_id,))
        cur.close()


def test_deleting_a_hunt_leaves_another_hunts_categories_alone():
    """A cascade keyed on the wrong column would take everything with it."""
    doomed = _insert_hunt("CascadeDoomed")
    survivor = _insert_hunt("CascadeSurvivor")
    try:
        _insert_hunt_category(doomed, 880000000000000011)
        _insert_hunt_category(survivor, 880000000000000012)

        _delete_hunt(doomed)

        assert _category_count(doomed) == 0
        assert _category_count(survivor) == 1
    finally:
        cur = mydb.cursor()
        for hid in (doomed, survivor):
            cur.execute("DELETE FROM hunt_category WHERE hunt_id = %s", (hid,))
            cur.execute("DELETE FROM hunts WHERE id = %s", (hid,))
        cur.close()

"""M6: verify migration 003 backfilled kind/status consistently (read-only).

Reads the migrated test DB and asserts the backfill invariants hold for the existing
rounds. Non-mutating, so it doesn't disturb shared test data; skips if the DB has no
rounds. The keep-updated hooks preserve these invariants (solve sets solve_time AND
status together; unsolve clears both), so this stays true after live solves too.
"""
import pytest


def _rounds():
    from bot.store import mydb
    cur = mydb.cursor(dictionary=True)
    cur.execute(
        "SELECT r.id, r.target_puzzle_id, r.kind, r.status, r.solve_time, r.archive_time, "
        "p.metameta AS meta_metameta "
        "FROM tag_group r LEFT JOIN puzzles p ON p.id = r.target_puzzle_id"
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def test_kind_backfill_matches_meta_shape():
    rows = _rounds()
    if not rows:
        pytest.skip("no rounds in test DB")
    for r in rows:
        if not r["target_puzzle_id"]:
            assert r["kind"] == "metaless", r
        elif r["meta_metameta"] == 1:
            assert r["kind"] == "metameta", r
        else:
            assert r["kind"] == "meta", r


def test_status_backfill_matches_lifecycle_times():
    rows = _rounds()
    if not rows:
        pytest.skip("no rounds in test DB")
    for r in rows:
        if r["archive_time"] is not None:
            expected = "archived"
        elif r["solve_time"] is not None:
            expected = "solved"
        else:
            expected = "active"
        assert r["status"] == expected, r

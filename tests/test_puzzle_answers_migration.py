"""Migration 065: backfill puzzle_answers + expected_answers from puzzles.solution.

LIVE MySQL (puzzlebot_test). Executes every statement of the real
065_puzzle_answers_backfill.sql file (via migrate.split_statements), not a copy.
The backfill runs over every puzzle in the table; assertions look only at the
puzzles each test inserts, and each test deletes them (answers cascade).
"""

import pathlib

from bot.store import mydb
from migrate import split_statements
from tests.conftest import live_hunt_id

BACKFILL = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "065_puzzle_answers_backfill.sql"


def _insert(solution, solved=0, status=None):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name, solution, solved, status) VALUES (%s, %s, %s, %s, %s)",
        (live_hunt_id(), "MigTest", solution, solved, status),
    )
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _delete(pid):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzle_answers WHERE puzzle_id = %s", (pid,))
    cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.close()
    mydb.commit()


def _run_backfill():
    cur = mydb.cursor()
    for stmt in split_statements(BACKFILL.read_text(encoding="utf-8")):
        cur.execute(stmt)
    cur.close()
    mydb.commit()


def _state(pid):
    cur = mydb.cursor()
    cur.execute("SELECT answer, position FROM puzzle_answers WHERE puzzle_id = %s ORDER BY position", (pid,))
    rows = cur.fetchall()
    cur.execute("SELECT expected_answers, expected_answers_confirmed FROM puzzles WHERE id = %s", (pid,))
    counts = cur.fetchone()
    cur.close()
    mydb.commit()
    return rows, counts


def _check(solution, solved, status, rows, expected, confirmed):
    pid = _insert(solution, solved, status)
    try:
        _run_backfill()
        got_rows, counts = _state(pid)
        assert got_rows == rows
        assert counts == (expected, confirmed)
    finally:
        _delete(pid)


def test_backfill_splits_slash_joined_answers():
    _check("APPLE/ PEAR", 1, "solved", [("APPLE", 1), ("PEAR", 2)], 2, 1)


def test_backfill_single_answer():
    _check("KIWI", 1, "solved", [("KIWI", 1)], 1, 1)


def test_backfill_tick_only_has_no_rows_and_expects_zero():
    _check("✅", 1, "solved", [], 0, 1)


def test_backfill_partial_stays_unsolved():
    _check("FIG", 0, "partially solved", [("FIG", 1)], 2, 0)


def test_backfill_unsolved_empty_untouched():
    pid_null = _insert(None)
    pid_empty = _insert("")
    try:
        _run_backfill()
        for pid in (pid_null, pid_empty):
            assert _state(pid) == ([], (1, 1))
    finally:
        _delete(pid_null)
        _delete(pid_empty)


def test_backfill_drops_empty_fragments():
    _check("A//B/", 1, "solved", [("A", 1), ("B", 2)], 2, 1)


def test_backfill_is_rerunnable():
    pid = _insert("APPLE/PEAR", 1, "solved")
    pid2 = _insert("FIG", 0, "partially solved")
    try:
        _run_backfill()
        first = (_state(pid), _state(pid2))
        _run_backfill()
        assert (_state(pid), _state(pid2)) == first
        assert first[1] == ([("FIG", 1)], (2, 0))
    finally:
        _delete(pid)
        _delete(pid2)


# Answers that contain '/' as part of ONE answer (live, 2026-11-26): built from
# their UTF-8 bytes so no backslash escaping can change them on the way here.
SPACEWALK = bytes.fromhex("282FE29795E383AEE29795292F").decode("utf-8")       # (/◕ヮ◕)/
BARRIER_TWO = bytes.fromhex("602F2F5C5C2F5C2F5C2F5C5C5C5C60").decode("utf-8")  # `//\\/\/\/\\\\`


def test_backfill_keeps_a_listed_slash_answer_whole():
    _check(SPACEWALK, 1, "solved", [(SPACEWALK, 1)], 1, 1)


def test_backfill_keeps_a_backslash_answer_whole():
    _check(BARRIER_TWO, 1, "solved", [(BARRIER_TWO, 1)], 1, 1)


def test_backfill_still_splits_an_answer_that_merely_resembles_one():
    # Only the exact listed bytes are whole answers; anything else still splits.
    _check("(/X)/", 1, "solved", [("(", 1), ("X)", 2)], 2, 1)

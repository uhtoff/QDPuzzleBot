"""Multiple answers, stage 1, task 2: AnswerData hydrated onto PuzzleData and a
dirty-gated reconcile in MySQLPuzzleJsonDb.commit.

LIVE MySQL (puzzlebot_test), following tests/test_tag_edges.py: each test inserts
its own puzzle (puzzle_answers rows cascade on puzzle delete) and deletes it in a
finally block.
"""

import datetime

from bot.store import PuzzleJsonDb, mydb
from bot.store.answer_data import AnswerData
from tests.conftest import live_hunt_id


def _insert_puzzle(name="AnswersTest"):
    cur = mydb.cursor()
    cur.execute("INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (live_hunt_id(), name))
    pid = cur.lastrowid
    cur.close()
    mydb.commit()
    return pid


def _insert_answer(puzzle_id, answer, position):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzle_answers (puzzle_id, answer, position) VALUES (%s, %s, %s)",
        (puzzle_id, answer, position),
    )
    rid = cur.lastrowid
    cur.close()
    mydb.commit()
    return rid


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()
    mydb.commit()


def _rows(puzzle_id):
    cur = mydb.cursor(dictionary=True)
    cur.execute(
        "SELECT id, answer, position FROM puzzle_answers WHERE puzzle_id = %s "
        "ORDER BY position, id", (puzzle_id,))
    rows = cur.fetchall()
    cur.close()
    return rows


def test_hydrate_answers_in_position_order():
    pid = _insert_puzzle()
    try:
        _insert_answer(pid, "SECOND", 2)
        _insert_answer(pid, "FIRST", 1)
        p = PuzzleJsonDb.get_by_attr(id=pid)
        assert [a.answer for a in p.answers] == ["FIRST", "SECOND"]
        assert isinstance(p.answers[0], AnswerData)
        assert p.answers[0].created_at is None or p.answers[0].created_at.tzinfo is not None
    finally:
        _delete_puzzle(pid)


def test_hydrate_answers_empty_list_not_none():
    pid = _insert_puzzle()
    try:
        p = PuzzleJsonDb.get_by_attr(id=pid)
        assert p.answers == []
        assert p.answers_dirty is False
    finally:
        _delete_puzzle(pid)


def test_commit_inserts_updates_and_deletes_answers():
    pid = _insert_puzzle()
    try:
        r1 = _insert_answer(pid, "ONE", 1)
        r2 = _insert_answer(pid, "TWO", 2)
        r3 = _insert_answer(pid, "THREE", 3)
        p = PuzzleJsonDb.get_by_attr(id=pid)
        assert len(p.answers) == 3
        p.answers.append(AnswerData(answer="NEW", position=4))
        p.answers[0].answer = "CHANGED"
        del p.answers[1]
        p.answers_dirty = True
        PuzzleJsonDb.commit(p)
        assert p.answers_dirty is False
        assert p.answers[-1].id != 0

        q = PuzzleJsonDb.get_by_attr(id=pid)
        assert [a.answer for a in q.answers] == ["CHANGED", "THREE", "NEW"]
        assert [a.id for a in q.answers][:2] == [r1, r3]
        assert q.answers[2].id not in (0, r1, r2, r3)
        assert q.answers[2].created_at is not None
        assert q.answers[2].created_at.tzinfo is not None
    finally:
        _delete_puzzle(pid)


def test_commit_skips_answers_when_not_dirty():
    pid = _insert_puzzle()
    try:
        _insert_answer(pid, "ONE", 1)
        _insert_answer(pid, "TWO", 2)
        p = PuzzleJsonDb.get_by_attr(id=pid)
        p.answers[0].answer = "CHANGED"
        del p.answers[1]
        PuzzleJsonDb.commit(p)
        assert [(r["answer"], r["position"]) for r in _rows(pid)] == [("ONE", 1), ("TWO", 2)]
    finally:
        _delete_puzzle(pid)


def test_commit_with_answers_none_leaves_rows_untouched():
    pid = _insert_puzzle()
    try:
        _insert_answer(pid, "ONE", 1)
        _insert_answer(pid, "TWO", 2)
        p = PuzzleJsonDb.get_by_attr(id=pid)
        p.answers = None
        p.answers_dirty = True
        PuzzleJsonDb.commit(p)
        assert [r["answer"] for r in _rows(pid)] == ["ONE", "TWO"]
    finally:
        _delete_puzzle(pid)


def test_expected_answer_columns_round_trip():
    pid = _insert_puzzle()
    try:
        p = PuzzleJsonDb.get_by_attr(id=pid)
        assert (p.expected_answers, p.expected_answers_confirmed) == (1, True)
        p.expected_answers = 3
        p.expected_answers_confirmed = False
        PuzzleJsonDb.commit(p)
        q = PuzzleJsonDb.get_by_attr(id=pid)
        assert q.expected_answers == 3
        assert not q.expected_answers_confirmed  # TINYINT reads back 0, like puzzles.solved
    finally:
        _delete_puzzle(pid)

import logging

from bot.core.answers import (
    derive_solution, ensure_loaded, record_add, record_change, record_partial,
    record_solve, record_unsolve, split_answers,
)
from bot.store.answer_data import AnswerData
from tests.conftest import make_puzzle


def state(p):
    return (p.solution, [a.answer for a in p.answers], p.expected_answers,
            p.expected_answers_confirmed, p.solved)


def test_split_answers():
    assert split_answers(" a / b//✅ ") == ["A", "B"]
    assert split_answers(None) == []


def test_solve_fresh():
    p = make_puzzle()
    record_solve(p, "kiwi", by="Ann")
    assert state(p) == ("KIWI", ["KIWI"], 1, True, True)
    assert p.answers[0].created_by == "Ann"
    assert p.answers_dirty is True


def test_solve_appends_to_partial():
    p = make_puzzle()
    record_partial(p, "FIG")
    record_solve(p, "lemon")
    assert state(p) == ("FIG/LEMON", ["FIG", "LEMON"], 2, True, True)
    assert p.answers_dirty is True


def test_solve_typed_slash_makes_two_answers():
    p = make_puzzle()
    record_solve(p, "a / b")
    assert state(p) == ("A/B", ["A", "B"], 2, True, True)
    assert p.answers_dirty is True


def test_solve_with_empty_text_solves_existing_partial():
    p = make_puzzle()
    record_partial(p, "FIG")
    record_solve(p, "")
    assert state(p) == ("FIG", ["FIG"], 1, True, True)
    assert p.answers_dirty is True


def test_complete_on_unanswered():
    p = make_puzzle()
    record_solve(p, "✅")
    assert state(p) == ("✅", [], 0, True, True)
    assert p.answers_dirty is True


def test_complete_on_answered_does_not_append_tick():
    p = make_puzzle()
    record_partial(p, "FIG")
    record_solve(p, "✅")
    assert state(p) == ("FIG", ["FIG"], 1, True, True)
    assert p.answers_dirty is True


def test_partial_counts_one_more():
    p = make_puzzle()
    record_partial(p, "fig")
    assert state(p) == ("FIG", ["FIG"], 2, False, False)
    record_partial(p, "lime")
    assert state(p) == ("FIG/LIME", ["FIG", "LIME"], 3, False, False)
    assert p.answers_dirty is True


def test_partial_empty_adds_nothing():
    p = make_puzzle()
    record_partial(p, "")
    assert state(p) == ("", [], 1, False, False)
    assert p.answers_dirty is True


def test_add_to_solved():
    p = make_puzzle()
    record_solve(p, "KIWI")
    p.answers_dirty = False
    record_add(p, "pear")
    assert state(p) == ("KIWI/PEAR", ["KIWI", "PEAR"], 2, True, True)
    assert p.answers_dirty is True


def test_change_keeps_ids_by_position():
    p = make_puzzle(solved=True, solution="A/B")
    p.answers = [AnswerData(id=11, answer="A", position=1, created_by="Ann"),
                 AnswerData(id=12, answer="B", position=2)]
    p.expected_answers = 2
    record_change(p, "a/c")
    assert [a.answer for a in p.answers] == ["A", "C"]
    assert [a.id for a in p.answers] == [11, 12]
    assert p.answers[0].created_by == "Ann"
    record_change(p, "x/y/z")
    assert [a.id for a in p.answers] == [11, 12, 0]
    assert [a.position for a in p.answers] == [1, 2, 3]
    record_change(p, "q")
    assert [a.id for a in p.answers] == [11]
    assert state(p) == ("Q", ["Q"], 1, True, True)
    assert p.answers_dirty is True


def test_unsolve_clears():
    p = make_puzzle()
    record_solve(p, "kiwi")
    p.answers_dirty = False
    record_unsolve(p)
    assert state(p) == ("", [], 1, True, False)
    assert p.answers_dirty is True


def test_ensure_loaded_from_legacy_string():
    p = make_puzzle(solved=True, solution="X/Y")
    rows = ensure_loaded(p)
    assert [(a.answer, a.position) for a in rows] == [("X", 1), ("Y", 2)]
    assert p.answers_dirty is True


def test_ensure_loaded_string_wins_on_drift(caplog):
    p = make_puzzle(solved=True, solution="FIG/LEMON")
    p.answers = [AnswerData(id=5, answer="FIG", position=1)]
    p.expected_answers = 2
    with caplog.at_level(logging.WARNING, logger="bot.core.answers"):
        record_add(p, "pear")
    assert p.solution == "FIG/LEMON/PEAR"
    assert p.answers[0].id == 5
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert p.answers_dirty is True


def test_ensure_loaded_leaves_matching_rows_alone(caplog):
    p = make_puzzle(solved=True, solution="APPLE/ PEAR")
    rows = [AnswerData(id=1, answer="APPLE", position=1),
            AnswerData(id=2, answer="PEAR", position=2)]
    p.answers = list(rows)
    with caplog.at_level(logging.WARNING):
        got = ensure_loaded(p)
    assert got[0] is rows[0] and got[1] is rows[1]
    assert p.answers_dirty is False
    assert not caplog.records


def test_invariant_holds_after_every_operation():
    p = make_puzzle()
    steps = [
        lambda: record_partial(p, "a"),
        lambda: record_partial(p, "b"),
        lambda: record_solve(p, "c"),
        lambda: record_add(p, "d"),
        lambda: record_change(p, "x/y"),
        lambda: record_unsolve(p),
        lambda: record_solve(p, "✅"),
    ]
    for step in steps:
        step()
        assert p.answers is not None
        assert p.solved == (len(p.answers) >= p.expected_answers)
        assert p.solution == derive_solution(p)

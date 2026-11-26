"""The pure answer seam: every answer write goes through here.

Keeps ``puzzle.solution`` (the derived display string), ``solved`` and the
expected-answer count consistent with each other. Pure: no DB, Discord or sheets
calls, and it never commits. Callers persist ``puzzle.answers`` when
``puzzle.answers_dirty`` is set.
"""
import datetime
import logging
from typing import List, Optional

from bot.core.constants import COMPLETE_MARK
from bot.store.answer_data import AnswerData
from bot.store.puzzle_data import PuzzleData

logger = logging.getLogger(__name__)


def split_answers(text: Optional[str]) -> List[str]:
    """Split on '/', strip, upper-case; drop empty fragments and the completion mark."""
    if not text:
        return []
    out = []
    for frag in text.split("/"):
        frag = frag.strip().upper()
        if frag and frag != COMPLETE_MARK:
            out.append(frag)
    return out


def _new_row(text: str, position: int, by: Optional[str]) -> AnswerData:
    return AnswerData(
        id=0,
        answer=text,
        position=position,
        created_by=by,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )


def _replace_by_position(puzzle: PuzzleData, rows: List[AnswerData], texts: List[str],
                         by: Optional[str]) -> None:
    """Set texts on existing rows by position (keeping ids), drop surplus, append extras."""
    rows.sort(key=lambda r: r.position)
    keep = rows[:len(texts)]
    for row, text in zip(keep, texts):
        row.answer = text
    next_pos = max((r.position for r in keep), default=0) + 1
    for text in texts[len(keep):]:
        keep.append(_new_row(text, next_pos, by))
        next_pos += 1
    rows[:] = keep
    puzzle.answers = rows
    puzzle.answers_dirty = True


def ensure_loaded(puzzle: PuzzleData) -> List[AnswerData]:
    """Return puzzle.answers, never None. The solution string wins on drift."""
    from_string = split_answers(puzzle.solution)
    if puzzle.answers is None:
        fresh: List[AnswerData] = []
        _replace_by_position(puzzle, fresh, from_string, None)
        return fresh
    rows = puzzle.answers
    current = [a.answer.strip().upper() for a in sorted(rows, key=lambda r: r.position)]
    if current != from_string:
        logger.warning(
            "puzzle %s: answers rows %r drifted from solution %r; the string wins",
            puzzle.id, current, puzzle.solution,
        )
        _replace_by_position(puzzle, rows, from_string, None)
    return rows


def derive_solution(puzzle: PuzzleData) -> str:
    rows = sorted(puzzle.answers or [], key=lambda r: r.position)
    if rows:
        return "/".join(r.answer for r in rows)
    if puzzle.expected_answers == 0 and puzzle.solved:
        return COMPLETE_MARK
    return ""


def _append(rows: List[AnswerData], texts: List[str], by: Optional[str]) -> None:
    next_pos = max((r.position for r in rows), default=0) + 1
    for text in texts:
        rows.append(_new_row(text, next_pos, by))
        next_pos += 1


def record_solve(puzzle: PuzzleData, text: str, *, by: Optional[str] = None) -> None:
    rows = ensure_loaded(puzzle)
    if text.strip() == COMPLETE_MARK and not rows:
        puzzle.expected_answers = 0
    else:
        _append(rows, split_answers(text), by)
        puzzle.expected_answers = len(rows)
    puzzle.expected_answers_confirmed = True
    puzzle.solved = True
    puzzle.solution = derive_solution(puzzle)
    puzzle.answers_dirty = True


def record_partial(puzzle: PuzzleData, text: str, *, by: Optional[str] = None) -> None:
    rows = ensure_loaded(puzzle)
    _append(rows, split_answers(text), by)
    puzzle.expected_answers = len(rows) + 1
    puzzle.expected_answers_confirmed = False
    puzzle.solved = False
    puzzle.solution = derive_solution(puzzle)
    puzzle.answers_dirty = True


def record_add(puzzle: PuzzleData, text: str, *, by: Optional[str] = None) -> None:
    rows = ensure_loaded(puzzle)
    _append(rows, split_answers(text), by)
    puzzle.expected_answers = len(rows)
    puzzle.solution = derive_solution(puzzle)
    puzzle.answers_dirty = True


def record_change(puzzle: PuzzleData, text: str, *, by: Optional[str] = None) -> None:
    rows = ensure_loaded(puzzle)
    _replace_by_position(puzzle, rows, split_answers(text), by)
    puzzle.expected_answers = len(puzzle.answers or [])
    puzzle.solution = derive_solution(puzzle)
    puzzle.answers_dirty = True


def record_unsolve(puzzle: PuzzleData) -> None:
    puzzle.answers = []
    puzzle.expected_answers = 1
    puzzle.expected_answers_confirmed = True
    puzzle.solved = False
    puzzle.solution = ""
    puzzle.answers_dirty = True

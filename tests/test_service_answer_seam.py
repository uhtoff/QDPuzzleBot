"""PuzzleService answer writers go through the pure answer seam (bot/core/answers.py)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle


def _service():
    from bot.cogs.puzzles import Puzzles

    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot).service


def _patched(commit_effect=None):
    stack = []
    mocks = {}
    for name in ("PuzzleJsonDb", "TagGroupJsonDb", "HuntJsonDb", "GuildSettingsDb"):
        p = patch(f"bot.core.puzzle_service.{name}")
        mocks[name] = p.start()
        stack.append(p)
    mocks["TagGroupJsonDb"].get_by_attr.return_value = None
    mocks["HuntJsonDb"].get_by_attr.return_value = make_hunt()
    mocks["GuildSettingsDb"].get.return_value = None
    if commit_effect:
        mocks["PuzzleJsonDb"].commit.side_effect = commit_effect
    return stack, mocks


@pytest.fixture
def patched():
    stack, mocks = _patched()
    yield mocks
    for p in stack:
        p.stop()


@pytest.mark.asyncio
async def test_service_solve_records_answer_rows(patched):
    p = make_puzzle(solution=None, solved=False)
    await _service().solve(None, make_hunt(), p, "kiwi", solved_by="Ann")
    assert [a.answer for a in p.answers] == ["KIWI"]
    assert p.answers[0].created_by == "Ann"
    assert p.expected_answers == 1


@pytest.mark.asyncio
async def test_service_partial_then_solve_counts(patched):
    p = make_puzzle(solution=None, solved=False)
    svc = _service()
    await svc.add_partial(None, make_hunt(), p, "fig")
    await svc.solve(None, make_hunt(), p, "lemon")
    assert [a.answer for a in p.answers] == ["FIG", "LEMON"]
    assert p.expected_answers == 2
    assert p.solution == "FIG/LEMON"


@pytest.mark.asyncio
async def test_service_unsolve_clears_answers(patched):
    p = make_puzzle(solution=None, solved=False)
    svc = _service()
    await svc.solve(None, make_hunt(), p, "kiwi")
    await svc.unsolve(None, make_hunt(), p)
    assert p.answers == []
    assert p.solution == ""


@pytest.mark.asyncio
async def test_service_commit_carries_dirty_answers():
    seen = []
    stack, _ = _patched(lambda pz: seen.append((pz.answers, pz.answers_dirty)))
    try:
        p = make_puzzle(solution=None, solved=False)
        await _service().solve(None, make_hunt(), p, "kiwi")
    finally:
        for s in stack:
            s.stop()
    answers, dirty = seen[0]
    assert isinstance(answers, list) and dirty is True

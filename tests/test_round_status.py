"""M6: a round's status tracks its meta (set on solve), no-op for feeders."""
from unittest.mock import MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_puzzle, make_round


def _svc_no_sheets():
    bot = MagicMock()
    bot.get_cog.return_value = None  # no GoogleSheets cog -> refresh_meta is a no-op
    return PuzzleService(bot)


def _hunt():
    h = HuntData()
    h.id = 10
    return h


@pytest.mark.asyncio
async def test_solving_a_meta_marks_its_round_solved():
    svc = _svc_no_sheets()
    meta_round = make_round(id=5, target_puzzle_id=1)   # round 5's meta IS puzzle 1
    puzzle = make_puzzle(id=1, metapuzzle=1, tags=[5])
    with patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.SheetsJsonDb"):
        rdb.get_by_attr.return_value = meta_round
        await svc.solve(None, _hunt(), puzzle, "ANSWER")
    assert meta_round.status == "solved"
    assert meta_round.solve_time is not None
    rdb.commit.assert_called()


@pytest.mark.asyncio
async def test_solving_a_feeder_leaves_round_status_active():
    svc = _svc_no_sheets()
    meta_round = make_round(id=5, target_puzzle_id=99)   # round 5's meta is some other puzzle
    puzzle = make_puzzle(id=2, tags=[5])
    with patch("bot.core.puzzle_service.RoundJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.SheetsJsonDb"):
        rdb.get_by_attr.return_value = meta_round
        await svc.solve(None, _hunt(), puzzle, "ANSWER")
    assert meta_round.status == "active"

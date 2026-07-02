"""solve/change_solution/add_solution must isolate a Sheets failure on their OWN
sheet write the same way they already isolate meta-refresh failures (#28).

Previously an uncaught HttpError from archive_puzzle_spreadsheet (solve) or
update_solution (change_solution/add_solution) propagated past the second
commit, the forum-tag update, and @pushes_board's board_changed push — even
though the DB mutation (solved/solution/solve_time) was already committed.
The dashboard then never saw the update, and the bang caller told the user
the change "may not have been saved" despite the DB saying otherwise.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.errors import SheetRefreshError
from tests.conftest import make_hunt, make_puzzle


def _svc_with_gsheet(gsheet_cog):
    from bot.core.puzzle_service import PuzzleService

    bot = MagicMock()
    bot.get_cog.return_value = gsheet_cog
    return PuzzleService(bot)


@patch("bot.core.puzzle_service.PuzzleJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.SheetsJsonDb")
def test_solve_archive_failure_still_commits_and_pushes(SheetsDb, RoundDb, PuzzleDb):
    import asyncio

    puzzle = make_puzzle(id=1, hunt_id=10, tags=[])
    hunt = make_hunt()
    RoundDb.get_by_attr.return_value = None

    gsheet_cog = MagicMock()
    gsheet_cog.archive_puzzle_spreadsheet = AsyncMock(side_effect=RuntimeError("Sheets 500"))
    svc = _svc_with_gsheet(gsheet_cog)
    svc.notifier = MagicMock(board_changed=AsyncMock())

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.solve(None, hunt, puzzle, "ANSWER"))

    assert puzzle.id in exc.value.failed_round_ids
    assert puzzle.solved is True
    assert PuzzleDb.commit.call_count == 2  # pre- and post-archive commit both ran
    svc.notifier.board_changed.assert_awaited_once()  # push still fires


@patch("bot.core.puzzle_service.PuzzleJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
def test_change_solution_sheet_failure_still_pushes(RoundDb, PuzzleDb):
    import asyncio

    puzzle = make_puzzle(id=1, hunt_id=10, solved=True, solution="OLD", tags=[])
    hunt = make_hunt()
    RoundDb.get_by_attr.return_value = None

    gsheet_cog = MagicMock()
    gsheet_cog.update_solution = AsyncMock(side_effect=RuntimeError("Sheets 500"))
    svc = _svc_with_gsheet(gsheet_cog)
    svc.notifier = MagicMock(board_changed=AsyncMock())

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.change_solution(None, hunt, puzzle, "NEW"))

    assert puzzle.id in exc.value.failed_round_ids
    assert puzzle.solution == "NEW"
    svc.notifier.board_changed.assert_awaited_once()


@patch("bot.core.puzzle_service.PuzzleJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
def test_add_solution_sheet_failure_still_pushes(RoundDb, PuzzleDb):
    import asyncio

    puzzle = make_puzzle(id=1, hunt_id=10, solved=True, solution="OLD", tags=[])
    hunt = make_hunt()
    RoundDb.get_by_attr.return_value = None

    gsheet_cog = MagicMock()
    gsheet_cog.update_solution = AsyncMock(side_effect=RuntimeError("Sheets 500"))
    svc = _svc_with_gsheet(gsheet_cog)
    svc.notifier = MagicMock(board_changed=AsyncMock())

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.add_solution(None, hunt, puzzle, "EXTRA"))

    assert puzzle.id in exc.value.failed_round_ids
    assert puzzle.solution == "OLD/EXTRA"
    svc.notifier.board_changed.assert_awaited_once()

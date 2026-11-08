"""solve/change_solution/add_solution must isolate a Sheets failure on their OWN
sheet write the same way they already isolate meta-refresh failures (#28).

Previously an uncaught HttpError from move_puzzle_to_solved_sheet (solve) or
update_solution (change_solution/add_solution) propagated past the second
commit and @pushes_board's board_changed push — even
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
    gsheet_cog.move_puzzle_to_solved_sheet = AsyncMock(side_effect=RuntimeError("Sheets 500"))
    svc = _svc_with_gsheet(gsheet_cog)
    svc.notifier = MagicMock(board_changed=AsyncMock())

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.solve(None, hunt, puzzle, "ANSWER"))

    assert puzzle.id in exc.value.failed_puzzle_ids
    assert exc.value.failed_group_ids == []
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

    assert puzzle.id in exc.value.failed_puzzle_ids
    assert exc.value.failed_group_ids == []
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

    assert puzzle.id in exc.value.failed_puzzle_ids
    assert exc.value.failed_group_ids == []
    assert puzzle.solution == "OLD/EXTRA"
    svc.notifier.board_changed.assert_awaited_once()


@patch("bot.core.puzzle_service.PuzzleJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.SheetsJsonDb")
def test_unsolve_reports_a_failed_meta_refresh_instead_of_swallowing_it(SheetsDb, RoundDb, PuzzleDb):
    """M27. solve collects its meta-refresh failures and raises; unsolve caught
    bare Exception and only logged. So !unsolve's `except SheetRefreshError`
    was unreachable and it printed ":white_check_mark: Sheets all restored."
    while the meta sheet still showed the retracted answer.

    The minimeta rollup below it stays best-effort on purpose -- solve treats
    its own the same way ("minis don't gate the round-meta toast"), and
    mirroring solve means mirroring that too.
    """
    import asyncio

    puzzle = make_puzzle(id=1, hunt_id=10, tags=[5])
    puzzle.solved = True
    puzzle.parent_puzzle_id = None
    puzzle.additional_sheets = []
    hunt = make_hunt()

    group = MagicMock()
    group.id = 5
    group.target_puzzle_id = 99          # not this puzzle: skip the round-undo commit
    RoundDb.get_by_attr.return_value = group
    PuzzleDb.get_all_from_hunt.return_value = []

    gsheet_cog = MagicMock()
    gsheet_cog.compute_insert_index = AsyncMock(return_value=3)
    gsheet_cog.restore_puzzle_from_solved_sheet = AsyncMock()
    svc = _svc_with_gsheet(gsheet_cog)
    svc.notifier = MagicMock(board_changed=AsyncMock())
    svc.refresh_target = AsyncMock(side_effect=RuntimeError("Sheets 500"))

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.unsolve(None, hunt, puzzle))

    assert exc.value.failed_group_ids == [5]
    assert exc.value.failed_puzzle_ids == []     # no archive move is involved
    # The unsolve itself is committed -- only the meta sheet went stale.
    assert puzzle.solved is False
    PuzzleDb.commit.assert_called()
    # pushes_board fires on SheetRefreshError by design, so the dashboard still refreshes.
    svc.notifier.board_changed.assert_awaited_once()

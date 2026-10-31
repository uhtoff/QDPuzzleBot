"""solve must re-render the roll-ups a solved puzzle FEEDS only after its tab has
moved to the solved spreadsheet (and that move is committed).

Roll-up tables link each member's name to its sheet. The move gives the tab a
new id in a different spreadsheet and deletes the live one, so a target rendered
before the move links to a tab that no longer exists.

The exception is the solved puzzle's OWN roll-up (the group whose target it is):
that table lives on the tab being moved, and the render path writes to the live
spreadsheet, so it must still render BEFORE the move — exactly as it always has.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.errors import SheetRefreshError
from tests.conftest import make_hunt, make_puzzle


def _group(id, target_puzzle_id):
    return SimpleNamespace(id=id, target_puzzle_id=target_puzzle_id,
                           solve_time=None, status="")


def _setup(PuzzleDb, TagGroupDb, groups, *, move_error=None, failing_group_ids=()):
    """Service whose cog move, refresh_target and refresh_minimeta all append to
    one shared log, so the test can read their relative order. Each refresh entry
    also records how many PuzzleJsonDb.commit calls had happened by then."""
    from bot.core.puzzle_service import PuzzleService

    log = []
    by_id = {g.id: g for g in groups}
    TagGroupDb.get_by_attr.side_effect = lambda id: by_id.get(id)

    gsheet_cog = MagicMock()

    async def move(puzzle):
        log.append(("move", puzzle.id))
        if move_error is not None:
            raise move_error
    gsheet_cog.move_puzzle_to_solved_sheet = AsyncMock(side_effect=move)

    bot = MagicMock()
    bot.get_cog.return_value = gsheet_cog
    svc = PuzzleService(bot)
    svc.notifier = MagicMock(board_changed=AsyncMock())

    async def refresh_target(hunt, group):
        log.append(("target", group.id, PuzzleDb.commit.call_count))
        if group.id in failing_group_ids:
            raise RuntimeError("Sheets 500")
    svc.refresh_target = AsyncMock(side_effect=refresh_target)

    async def refresh_minimeta(hunt, parent):
        log.append(("minimeta", parent.id, PuzzleDb.commit.call_count))
    svc.refresh_minimeta = AsyncMock(side_effect=refresh_minimeta)
    return svc, log, gsheet_cog


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_every_target_a_feeder_feeds_renders_after_the_move_is_committed(PuzzleDb, TagGroupDb, SheetsDb):
    feeder = make_puzzle(id=1, tags=[5, 6])
    svc, log, _ = _setup(PuzzleDb, TagGroupDb, [_group(5, 100), _group(6, 200)])

    assert asyncio.run(svc.solve(None, make_hunt(), feeder, "ANSWER")) is True

    # Both targets after the move, in tag order, each seeing BOTH commits (the
    # second is the one that persists the moved tab's new id).
    assert log == [("move", 1), ("target", 5, 2), ("target", 6, 2)]


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_a_failed_move_still_refreshes_every_target_and_names_the_puzzle(PuzzleDb, TagGroupDb, SheetsDb):
    feeder = make_puzzle(id=1, tags=[5, 6])
    svc, log, _ = _setup(PuzzleDb, TagGroupDb, [_group(5, 100), _group(6, 200)],
                         move_error=RuntimeError("Sheets 500"))

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.solve(None, make_hunt(), feeder, "ANSWER"))

    assert exc.value.failed_puzzle_ids == [1]
    assert exc.value.failed_group_ids == []
    assert log == [("move", 1), ("target", 5, 2), ("target", 6, 2)]
    svc.notifier.board_changed.assert_awaited_once()


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_a_failed_refresh_still_moves_and_refreshes_the_rest(PuzzleDb, TagGroupDb, SheetsDb):
    feeder = make_puzzle(id=1, tags=[5, 6])
    svc, log, gsheet_cog = _setup(PuzzleDb, TagGroupDb, [_group(5, 100), _group(6, 200)],
                                  failing_group_ids={5})

    with pytest.raises(SheetRefreshError) as exc:
        asyncio.run(svc.solve(None, make_hunt(), feeder, "ANSWER"))

    assert exc.value.failed_group_ids == [5]
    assert exc.value.failed_puzzle_ids == []
    gsheet_cog.move_puzzle_to_solved_sheet.assert_awaited_once_with(feeder)
    assert log == [("move", 1), ("target", 5, 2), ("target", 6, 2)]
    assert PuzzleDb.commit.call_count == 2


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_a_meta_renders_its_own_table_before_its_tab_moves_and_its_metameta_after(PuzzleDb, TagGroupDb, SheetsDb):
    # Puzzle 100 is the meta of group 5 and feeds metameta group 6 (target 200).
    meta = make_puzzle(id=100, tags=[5, 6])
    own, metameta = _group(5, 100), _group(6, 200)
    svc, log, _ = _setup(PuzzleDb, TagGroupDb, [own, metameta])

    asyncio.run(svc.solve(None, make_hunt(), meta, "ANSWER"))

    # Own table on the still-live tab (one commit so far); metameta after the move.
    assert log == [("target", 5, 1), ("move", 100), ("target", 6, 2)]
    assert own.status == "solved" and own.solve_time is not None
    assert metameta.status == "" and metameta.solve_time is None


@patch("bot.core.puzzle_service.SheetsJsonDb")
@patch("bot.core.puzzle_service.TagGroupJsonDb")
@patch("bot.core.puzzle_service.PuzzleJsonDb")
def test_a_solved_mini_rolls_up_onto_its_minimeta_after_the_move(PuzzleDb, TagGroupDb, SheetsDb):
    mini = make_puzzle(id=7, tags=[5], parent_puzzle_id=3)
    parent = make_puzzle(id=3)
    svc, log, _ = _setup(PuzzleDb, TagGroupDb, [_group(5, 100)])
    PuzzleDb.get_by_attr.return_value = parent

    asyncio.run(svc.solve(None, make_hunt(), mini, "ANSWER"))

    assert log == [("move", 7), ("target", 5, 2), ("minimeta", 3, 2)]

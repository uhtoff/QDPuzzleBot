"""Confirm-on-solve for the feeder pool, exercised through the shared seam.

Uses a real PuzzleService — `make_cog()` stubs `.service` with a MagicMock, which
would make every assertion here pass regardless of the implementation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_hunt, make_puzzle


def _service():
    return PuzzleService(MagicMock())


def _mini(pid=101, parent_id=100):
    return make_puzzle(id=pid, name=f"Mini {pid}",
                       puzzle_type="Mini", parent_puzzle_id=parent_id)


async def test_solving_a_mini_confirms_its_claims():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(_mini(), confirmed=True)
    claim_db.set_confirmed_for_target.assert_called_once_with(101, True)


async def test_unsolving_a_mini_demotes_its_claims():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(_mini(), confirmed=False)
    claim_db.set_confirmed_for_target.assert_called_once_with(101, False)


async def test_a_non_mini_puzzle_confirms_nothing():
    """An ordinary puzzle solving must not touch any pool — only a Mini's solve is
    proof about a feeder."""
    svc = _service()
    ordinary = make_puzzle(id=200, name="Ordinary")
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(ordinary, confirmed=True)
    claim_db.set_confirmed_for_target.assert_not_called()


async def test_a_mini_with_no_parent_confirms_nothing():
    """Type alone is not enough — an orphaned Mini has no pool to confirm against."""
    svc = _service()
    orphan = make_puzzle(id=201, name="Orphan", puzzle_type="Mini")
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(orphan, confirmed=True)
    claim_db.set_confirmed_for_target.assert_not_called()


async def test_a_mini_with_its_type_retagged_still_confirms_on_solve():
    """puzzle_type is user-settable via !type/the dashboard, and this codebase
    deliberately never gates on it (bot/web/data.py). parent_puzzle_id alone is
    the structural signal -- a solver running !type must not silently break
    confirm-on-solve for a mini's feeder claims."""
    svc = _service()
    retagged = make_puzzle(
        id=101, name="Mini 101", puzzle_type="Cryptic", parent_puzzle_id=100)
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(retagged, confirmed=True)
    claim_db.set_confirmed_for_target.assert_called_once_with(101, True)


async def test_confirming_targets_the_solved_mini_and_not_its_sibling():
    """Two minis under the same parent — a hook that confirmed by POOL rather than
    by TARGET would pass with only one mini in the test."""
    svc = _service()
    mini_a, mini_b = _mini(pid=101), _mini(pid=102)
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(mini_a, confirmed=True)
    claim_db.set_confirmed_for_target.assert_called_once_with(mini_a.id, True)
    assert claim_db.set_confirmed_for_target.call_args.args[0] != mini_b.id


async def test_solve_calls_the_hook():
    """Through service.solve() itself, not the helper — this is the seam both the
    Discord and web edges call, and the whole point of hooking here."""
    svc = _service()
    mini = _mini()
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch.object(svc, "confirm_feeder_claims", new=AsyncMock()) as hook:
        try:
            await svc.solve(MagicMock(), make_hunt(), mini, "SEABIRDS")
        except Exception:
            pass   # sheet/archive plumbing is not what this test is about
    hook.assert_awaited_once()
    assert hook.await_args.args[0] is mini


async def test_unsolve_calls_the_hook():
    """The inverse, through the same seam."""
    svc = _service()
    svc.bot.get_cog.return_value = None  # no GoogleSheets cog -> skip the (unguarded)
    # sheet-restore branch in unsolve(), which would otherwise raise on a bare
    # MagicMock before reaching the hook — matches the pattern used throughout the
    # rest of the suite (e.g. tests/test_refresh_minimeta.py, test_solve_equivalence.py).
    mini = _mini()
    mini.solved = True
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.SheetsJsonDb"), \
         patch.object(svc, "confirm_feeder_claims", new=AsyncMock()) as hook:
        try:
            await svc.unsolve(MagicMock(), make_hunt(), mini)
        except Exception:
            pass
    hook.assert_awaited_once()
    assert hook.await_args.args[0] is mini

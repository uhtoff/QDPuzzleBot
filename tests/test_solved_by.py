"""A solve records who solved it (puzzles.solved_by), for the chat extension's
"Solved: MINE (Uhtoff)" row. Both entry points -- the !solve bang and the web
solve route -- pass the solver through the one service seam; unsolve clears it."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle, make_web_app, start_patches, stop_patches


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None   # no sheets
    svc = PuzzleService(bot)
    svc.notifier = MagicMock(board_changed=AsyncMock())
    return svc


@pytest.mark.asyncio
async def test_service_solve_records_the_solver_before_committing():
    svc = _svc()
    puzzle = make_puzzle(id=1, tags=[], solved=False, solution=None)
    committed = []
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"):
        pdb.commit.side_effect = lambda p: committed.append(p.solved_by)
        await svc.solve(None, make_hunt(), puzzle, "MINE", solved_by="Uhtoff")
    assert puzzle.solved_by == "Uhtoff"
    assert committed[0] == "Uhtoff"


@pytest.mark.asyncio
async def test_service_unsolve_clears_the_solver():
    svc = _svc()
    puzzle = make_puzzle(id=1, tags=[], solved=True, solution="MINE")
    puzzle.solved_by = "Uhtoff"
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch("bot.core.puzzle_service.TagGroupJsonDb"):
        await svc.unsolve(None, make_hunt(), puzzle)
    assert puzzle.solved_by is None


@pytest.mark.asyncio
async def test_bang_solve_passes_the_authors_display_name():
    puzzle = make_puzzle(id=1, solved=False)
    cog = make_cog(puzzle=puzzle, hunt=make_hunt())
    cog.service.solve = AsyncMock(return_value=False)   # stop after the service call
    cog.get_guild = MagicMock(return_value=None)
    ctx = make_ctx()
    ctx.author.display_name = "Uhtoff"
    await cog._solve_impl(ctx, "MINE")
    assert cog.service.solve.await_args.kwargs["solved_by"] == "Uhtoff"


@pytest.mark.asyncio
async def test_web_solve_passes_the_users_display_name():
    cog = MagicMock()
    cog.service.solve = AsyncMock()
    cog.web_post_solved_embed = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)
    pdb = MagicMock()
    pdb.get_by_attr.return_value = make_puzzle(id=9, solved=False, solution=None)
    hdb = MagicMock()
    hdb.get_by_attr.return_value = SimpleNamespace(id=10, uid="U1")
    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "uhtoff", "global_name": "Uhtoff"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions._refresh_parent_minimeta_pin", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().post("/api/puzzles/9/solve", json={"solution": "MINE", "mode": "full"})
    finally:
        stop_patches(patches)
    assert resp.status_code == 200
    assert cog.service.solve.await_args.kwargs["solved_by"] == "Uhtoff"

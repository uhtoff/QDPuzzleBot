"""POST /api/puzzles/<id>/solve must mirror the state invariants the bang
commands enforce: !solve/!partial refuse an already-solved puzzle, and
!change_solution/!add_solution on an unsolved puzzle fall back to solve/partial
instead of stamping solve_time or "Partially Solved" status without solving.
Previously the web route dispatched straight to the service call for every
mode with no solved-state check (finding #1)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle, make_web_app, start_patches, stop_patches


def _ctx():
    return {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}


@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.solve = AsyncMock()
    cog.service.add_partial = AsyncMock()
    cog.service.change_solution = AsyncMock()
    cog.service.add_solution = AsyncMock()
    cog.web_post_solved_embed = AsyncMock()
    cog.web_post_solution_update_embed = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()

    app = make_web_app("actions", cog=cog)

    hdb = MagicMock()
    hdb.get_by_attr.return_value = SimpleNamespace(id=10, uid="U1")
    pdb = MagicMock()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(_ctx(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions._refresh_parent_minimeta_pin", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, pdb=pdb)
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_solve_rejects_already_solved_puzzle(web):
    web.pdb.get_by_attr.return_value = make_puzzle(id=9, solved=True, solution="BLUE")

    resp = await web.client.post("/api/puzzles/9/solve", json={"solution": "GREY", "mode": "full"})

    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "already_solved"
    web.cog.service.solve.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_rejects_already_solved_puzzle(web):
    web.pdb.get_by_attr.return_value = make_puzzle(id=9, solved=True, solution="BLUE")

    resp = await web.client.post("/api/puzzles/9/solve", json={"solution": "GREY", "mode": "partial"})

    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "already_solved"
    web.cog.service.add_partial.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_on_unsolved_puzzle_falls_back_to_solve(web):
    web.pdb.get_by_attr.return_value = make_puzzle(id=9, solved=False, solution=None)

    resp = await web.client.post("/api/puzzles/9/solve", json={"solution": "GREY", "mode": "change"})

    assert resp.status_code == 200
    web.cog.service.solve.assert_awaited_once()
    web.cog.service.change_solution.assert_not_awaited()
    web.cog.web_post_solved_embed.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_on_unsolved_puzzle_falls_back_to_partial(web):
    web.pdb.get_by_attr.return_value = make_puzzle(id=9, solved=False, solution=None)

    resp = await web.client.post("/api/puzzles/9/solve", json={"solution": "GREY", "mode": "add"})

    assert resp.status_code == 200
    web.cog.service.add_partial.assert_awaited_once()
    web.cog.service.add_solution.assert_not_awaited()
    web.cog.web_post_solution_update_embed.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_on_solved_puzzle_still_calls_change(web):
    web.pdb.get_by_attr.return_value = make_puzzle(id=9, solved=True, solution="BLUE")

    resp = await web.client.post("/api/puzzles/9/solve", json={"solution": "GREY", "mode": "change"})

    assert resp.status_code == 200
    web.cog.service.change_solution.assert_awaited_once()
    web.cog.service.solve.assert_not_awaited()

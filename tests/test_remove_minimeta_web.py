"""The remove-minimeta route's except-clause ORDER is load-bearing (M35).

`SheetRefreshError` subclasses `RuntimeError`, and the route catches both. If
the two clauses are ever swapped, a benign stale-sheet warning is reported as a
400 `not_minimeta` for an operation that in fact SUCCEEDED -- the minis and the
rollup table are already gone by then.

Until now a source comment was the only thing guarding that. Both sibling routes
have this contract route-tested (tests/test_delete_mini_web.py:120,
tests/test_change_group_type_web.py:132); this one had no route test at all --
nothing posted to /remove-minimeta or /make-minimeta.

The archived case is here too because this is one of the fifteen routes moved
onto the shared _route_ctx prologue: it checks that migration kept the guard.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.errors import SheetRefreshError
from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)


@asynccontextmanager
async def _noop_lock(_id):
    yield


def _web(remove_side_effect=None, archived=False, organiser=True):
    cog = MagicMock()
    cog.service.remove_minimeta = AsyncMock(side_effect=remove_side_effect)
    cog.service.refresh_unassigned_safe = AsyncMock()
    cog.web_refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)

    pdb = MagicMock()
    pdb.get_by_attr.return_value = make_puzzle(id=9)
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle",
              AsyncMock(return_value=(make_hunt_ctx(archived=archived), None))),
        patch("bot.web.actions.require_organiser",
              AsyncMock(return_value=None if organiser else ({"error": "forbidden"}, 403))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    start_patches(patches)
    return SimpleNamespace(app=app, client=app.test_client(), cog=cog, patches=patches)


async def _post(web):
    try:
        resp = await web.client.post("/api/puzzles/9/remove-minimeta")
        return resp, await resp.get_json()
    finally:
        stop_patches(web.patches)


@pytest.mark.asyncio
async def test_a_stale_sheet_is_a_warning_not_a_400():
    """Swap the two except clauses and this becomes 400 not_minimeta for an
    operation that succeeded. That is the whole finding."""
    resp, body = await _post(_web(remove_side_effect=SheetRefreshError(failed_group_ids=[5])))

    assert resp.status_code == 200, body
    assert body["ok"] is True
    assert "sheet_warning" in body
    assert "5" in body["sheet_warning"]


@pytest.mark.asyncio
async def test_a_genuine_not_minimeta_is_still_a_400():
    """The keeper. Without it, deleting the RuntimeError clause entirely would
    pass the test above -- the pair is what pins the ORDER rather than the mere
    presence of one handler."""
    resp, body = await _post(_web(remove_side_effect=RuntimeError("not_minimeta")))

    assert resp.status_code == 400
    assert body["error"] == "not_minimeta"


@pytest.mark.asyncio
async def test_a_clean_removal_carries_no_warning():
    resp, body = await _post(_web())

    assert resp.status_code == 200
    assert body["ok"] is True
    assert "sheet_warning" not in body


@pytest.mark.asyncio
async def test_an_archived_hunt_is_refused_before_anything_is_removed():
    """Also a check on the _route_ctx migration: this route is one of the
    fifteen moved onto the shared prologue."""
    web = _web(archived=True)
    resp, body = await _post(web)

    assert resp.status_code == 403
    assert body["error"] == "hunt_archived"
    web.cog.service.remove_minimeta.assert_not_awaited()

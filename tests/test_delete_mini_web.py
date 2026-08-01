"""Web edge for deleting ONE mini-puzzle: DELETE /api/puzzles/<minimeta>/minis/<mini>.

Command parity with !delete_mini. The route is nested under the parent, so the
mini/minimeta pairing is verified rather than assumed, and the parent's pinned
mini-list is refreshed the way the bang path does it.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)


@asynccontextmanager
async def _noop_lock(_id):
    yield


def _web(*, mini=None, delete_side_effect=None, organiser=True, archived=False):
    """Build the patched app. `mini` is what PuzzleJsonDb.get_by_attr returns for
    the mini lookup (None = missing row)."""
    minimeta = make_puzzle(id=3, name="Cluster", puzzle_type="Minimeta")
    cog = MagicMock()
    cog.service.delete_mini = AsyncMock(
        return_value=minimeta, side_effect=delete_side_effect)
    cog.web_refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
    ctx["hunt"]["archived"] = archived
    pdb = MagicMock()
    pdb.get_by_attr.return_value = mini
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser",
              AsyncMock(return_value=None if organiser else ({"error": "forbidden"}, 403))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    return SimpleNamespace(app=app, cog=cog, patches=patches, minimeta=minimeta)


@asynccontextmanager
async def _client(**kwargs):
    web = _web(**kwargs)
    start_patches(web.patches)
    try:
        web.client = web.app.test_client()
        yield web
    finally:
        stop_patches(web.patches)


@pytest.mark.asyncio
async def test_deletes_the_mini_and_returns_ok():
    async with _client(mini=make_puzzle(id=9, name="Word Search", parent_puzzle_id=3)) as web:
        resp = await web.client.delete("/api/puzzles/3/minis/9")

        assert resp.status_code == 200
        assert (await resp.get_json())["ok"] is True
        assert web.cog.service.delete_mini.await_args[0][2].id == 9


@pytest.mark.asyncio
async def test_refreshes_the_parent_pin_not_the_mini():
    async with _client(mini=make_puzzle(id=9, parent_puzzle_id=3)) as web:
        await web.client.delete("/api/puzzles/3/minis/9")

        web.cog.web_refresh_info.assert_awaited_once()
        assert web.cog.web_refresh_info.await_args[0][2] is web.minimeta


@pytest.mark.asyncio
async def test_mini_belonging_to_another_minimeta_is_404():
    """The pairing is checked: this route must not delete an unrelated puzzle."""
    async with _client(mini=make_puzzle(id=9, parent_puzzle_id=99)) as web:
        resp = await web.client.delete("/api/puzzles/3/minis/9")

        assert resp.status_code == 404
        assert (await resp.get_json())["error"] == "mini_not_found"
        web.cog.service.delete_mini.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_mini_is_404():
    async with _client(mini=None) as web:
        resp = await web.client.delete("/api/puzzles/3/minis/9")

        assert resp.status_code == 404
        web.cog.service.delete_mini.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_organiser_is_refused():
    async with _client(mini=make_puzzle(id=9, parent_puzzle_id=3), organiser=False) as web:
        resp = await web.client.delete("/api/puzzles/3/minis/9")

        assert resp.status_code == 403
        web.cog.service.delete_mini.assert_not_awaited()


@pytest.mark.asyncio
async def test_archived_hunt_is_refused():
    async with _client(mini=make_puzzle(id=9, parent_puzzle_id=3), archived=True) as web:
        resp = await web.client.delete("/api/puzzles/3/minis/9")

        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "hunt_archived"
        web.cog.service.delete_mini.assert_not_awaited()


@pytest.mark.asyncio
async def test_sheet_refresh_failure_is_a_warning_not_a_failure():
    """The row and thread are already gone by then — a 500 would invite a retry
    against a mini that no longer exists."""
    from bot.core.puzzle_service import SheetRefreshError

    async with _client(mini=make_puzzle(id=9, parent_puzzle_id=3),
                       delete_side_effect=SheetRefreshError(["Cluster"])) as web:
        resp = await web.client.delete("/api/puzzles/3/minis/9")

        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["ok"] is True
        assert "sheet_warning" in body

"""The web unsolve route must surface a stale meta sheet, not 500 on it (M27).

Once service.unsolve reports its failed meta refreshes instead of swallowing
them, this route becomes the reason that change cannot ship alone: it caught
only discord.RateLimited/HTTPException, so a SheetRefreshError would escape as
an unhandled 500 for an operation that actually SUCCEEDED. Today's silent lie
would have become a louder one.

Its solve sibling has surfaced this as a warning since M5; this pins the pair.
"""
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.errors import SheetRefreshError
from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)


@asynccontextmanager
async def _noop_lock(_id):
    yield


def _web(unsolve_side_effect=None):
    cog = MagicMock()
    cog.service.unsolve = AsyncMock(side_effect=unsolve_side_effect)
    cog.web_post_unsolved = AsyncMock()
    app = make_web_app("actions", cog=cog)

    puzzle = make_puzzle(id=7)
    puzzle.solved = True
    puzzle.solution = "ANSWER"
    pdb = MagicMock()
    pdb.get_by_attr.return_value = puzzle
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions._refresh_parent_minimeta_pin", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    start_patches(patches)
    return SimpleNamespace(app=app, client=app.test_client(), cog=cog, patches=patches)


@pytest.mark.asyncio
async def test_a_stale_meta_sheet_is_a_warning_not_a_500():
    """The operation succeeded; only a downstream sheet went stale."""
    web = _web(unsolve_side_effect=SheetRefreshError(failed_group_ids=[5]))
    try:
        resp = await web.client.post("/api/puzzles/7/unsolve")
        body = await resp.get_json()
    finally:
        stop_patches(web.patches)

    assert resp.status_code == 200, f"got {resp.status_code}: {body}"
    assert body["ok"] is True
    assert "sheet_warning" in body
    assert "5" in body["sheet_warning"]
    # The rest of the route still ran -- the unsolve is real.
    web.cog.web_post_unsolved.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_clean_unsolve_carries_no_warning():
    """Two cases, so 'always warn' and 'warn on failure' disagree."""
    web = _web()
    try:
        resp = await web.client.post("/api/puzzles/7/unsolve")
        body = await resp.get_json()
    finally:
        stop_patches(web.patches)

    assert resp.status_code == 200
    assert body["ok"] is True
    assert "sheet_warning" not in body

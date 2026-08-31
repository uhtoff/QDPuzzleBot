"""PATCH /api/puzzles/<id> must refuse on an archived hunt (M29 stage 2).

Found by mutation while finishing the route-prologue extraction: flipping
update_puzzle's call to `allow_archived=True` left the whole suite green. No
test drove this route as a WEB route at all -- tests/test_update_puzzle_field.py
exercises the service layer directly, which never sees the archived gate.

That is the second route caught this way (stage 1 found delete_puzzle in the
same state), and it is the case FOR the extraction rather than against it: with
the prologue hand-rolled 36 times a missing guard was one indistinguishable copy
among many, and no single mutation could ask the question. Behind one helper,
flipping one keyword asks it for every route at once.
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


def _web(archived):
    cog = MagicMock()
    cog.service.update_puzzle_field = AsyncMock()
    cog.web_refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)

    pdb = MagicMock()
    pdb.get_by_attr.return_value = make_puzzle(id=7)
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle",
              AsyncMock(return_value=(make_hunt_ctx(archived=archived), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    start_patches(patches)
    return SimpleNamespace(client=app.test_client(), cog=cog, patches=patches)


async def _patch_status(web):
    try:
        resp = await web.client.patch("/api/puzzles/7", json={"status": "Solved"})
        return resp, await resp.get_json()
    finally:
        stop_patches(web.patches)


@pytest.mark.asyncio
async def test_an_archived_hunt_refuses_the_update():
    web = _web(archived=True)
    resp, body = await _patch_status(web)

    assert resp.status_code == 403, body
    assert body["error"] == "hunt_archived"
    # Refused BEFORE any mutation is attempted.
    web.cog.service.update_puzzle_field.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_live_hunt_still_accepts_the_update():
    """The keeper: two hunt states, so "refuse everything" and "refuse when
    archived" disagree."""
    web = _web(archived=False)
    resp, body = await _patch_status(web)

    assert resp.status_code == 200, body
    web.cog.service.update_puzzle_field.assert_awaited_once()

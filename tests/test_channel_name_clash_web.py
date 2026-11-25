"""Web create/rename: a channel-name clash is a 409 the dashboard turns into an
Accept/Cancel prompt; resending with accept_channel_suffix uses the -N name."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.errors import ChannelNameClash
from tests.conftest import (make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)


@pytest.fixture
def web():
    cog = MagicMock()
    puzzle = make_puzzle(id=9, channel_id=100)
    cog.service.create_puzzle = AsyncMock(return_value=puzzle)
    cog.service.rename_puzzle = AsyncMock()
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb"),
        patch("bot.store.PuzzleJsonDb"),
        patch("bot.store.TagGroupJsonDb"),
    ]
    started = start_patches(patches)
    hdb, pdb, rdb = started[5], started[6], started[7]
    pdb.get_by_attr.return_value = puzzle
    pdb.check_duplicates_in_hunt.return_value = False
    rdb.get_by_attr.return_value = None
    hdb.get_by_attr.return_value = MagicMock()
    try:
        yield SimpleNamespace(client=app.test_client(), svc=cog.service)
    finally:
        stop_patches(patches)


async def test_create_clash_is_409_with_names_and_asks_first(web):
    web.svc.create_puzzle.side_effect = ChannelNameClash("general", "general-1")
    resp = await web.client.post("/api/hunts/U1/puzzles", json={"name": "General"})
    assert resp.status_code == 409
    assert await resp.get_json() == {"error": "channel_name_clash",
                                     "channel": "general", "suggested": "general-1"}
    assert web.svc.create_puzzle.await_args.kwargs["on_channel_clash"] == "ask"


async def test_create_with_accept_uses_suffix_mode(web):
    resp = await web.client.post("/api/hunts/U1/puzzles",
                                 json={"name": "General", "accept_channel_suffix": True})
    assert resp.status_code == 200
    assert web.svc.create_puzzle.await_args.kwargs["on_channel_clash"] == "suffix"


async def test_accept_flag_must_be_true_not_truthy(web):
    await web.client.post("/api/hunts/U1/puzzles",
                          json={"name": "General", "accept_channel_suffix": "yes"})
    assert web.svc.create_puzzle.await_args.kwargs["on_channel_clash"] == "ask"


async def test_rename_clash_is_409_then_accept_suffixes(web):
    web.svc.rename_puzzle.side_effect = ChannelNameClash("general", "general-1")
    resp = await web.client.post("/api/puzzles/9/rename", json={"name": "General"})
    assert resp.status_code == 409
    assert (await resp.get_json())["suggested"] == "general-1"
    assert web.svc.rename_puzzle.await_args.kwargs["on_channel_clash"] == "ask"

    web.svc.rename_puzzle.side_effect = None
    resp = await web.client.post("/api/puzzles/9/rename",
                                 json={"name": "General", "accept_channel_suffix": True})
    assert resp.status_code == 200
    assert web.svc.rename_puzzle.await_args.kwargs["on_channel_clash"] == "suffix"

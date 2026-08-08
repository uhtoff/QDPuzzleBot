"""The web convert-to-meta route must pass the live guild to the service so the
Discord-side effects (channel glyph/name refresh, forum tags) actually run —
passing guild=None silently no-ops every guild-gated side effect."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_round,
                            make_web_app, start_patches, stop_patches)


@asynccontextmanager
async def _noop_lock(_id):
    yield


@pytest.fixture
def web():
    guild = MagicMock()
    cog = MagicMock()
    cog.service.convert_to_type = AsyncMock(return_value=(make_round(id=5), "promoted"))
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog, get_guild=guild)

    ctx = make_hunt_ctx()
    pdb = MagicMock(); pdb.get_by_attr.return_value = make_puzzle(id=7, is_target=False)
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, guild=guild)
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_convert_to_meta_passes_live_guild_not_none(web):
    resp = await web.client.post("/api/puzzles/7/convert-to-meta")
    assert resp.status_code == 200
    web.cog.service.convert_to_type.assert_awaited_once()
    passed_guild = web.cog.service.convert_to_type.await_args.args[0]
    assert passed_guild is web.guild, "route must resolve and pass the live guild"


@pytest.mark.asyncio
async def test_convert_to_meta_refuses_when_the_organiser_gate_denies(web):
    """Levelled at organiser (was admin, while the group-keyed change-type route
    was already organiser — an organiser could reach the same conversion by
    addressing the group). The gate still has to be consulted."""
    with patch("bot.web.actions.require_organiser",
               AsyncMock(return_value=({"error": "insufficient_role"}, 403))):
        resp = await web.client.post("/api/puzzles/7/convert-to-meta")
    assert resp.status_code == 403
    web.cog.service.convert_to_type.assert_not_awaited()

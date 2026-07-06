"""The web convert-to-meta route must pass the live guild to the service so the
Discord-side effects (channel glyph/name refresh, forum tags) actually run —
passing guild=None silently no-ops every guild-gated side effect."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle, make_round


@asynccontextmanager
async def _noop_lock(_id):
    yield


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    guild = MagicMock()
    cog = MagicMock()
    cog.service.convert_to_type = AsyncMock(return_value=(make_round(id=5), "promoted"))
    cog.service.refresh_unassigned_safe = AsyncMock()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = guild
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    pdb = MagicMock(); pdb.get_by_attr.return_value = make_puzzle(id=7, is_target=False)
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, guild=guild)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_convert_to_meta_passes_live_guild_not_none(web):
    resp = await web.client.post("/api/puzzles/7/convert-to-meta")
    assert resp.status_code == 200
    web.cog.service.convert_to_type.assert_awaited_once()
    passed_guild = web.cog.service.convert_to_type.await_args.args[0]
    assert passed_guild is web.guild, "route must resolve and pass the live guild"

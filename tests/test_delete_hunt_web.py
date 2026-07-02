from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt


@asynccontextmanager
async def _noop_lock(_ids):
    yield


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.delete_hunt = AsyncMock(return_value=[])
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()
    pdb = MagicMock()
    pdb.get_all_from_hunt.return_value = []

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.web.actions.puzzles_lock", _noop_lock),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, pdb=pdb)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_delete_hunt_web_deletes(web):
    resp = await web.client.delete("/api/hunts/U1")
    assert resp.status_code == 200
    web.cog.service.delete_hunt.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_hunt_web_locks_member_puzzles(web):
    web.pdb.get_all_from_hunt.return_value = [MagicMock(id=3), MagicMock(id=1)]
    with patch("bot.web.actions.puzzles_lock") as lock_mock:
        lock_mock.return_value.__aenter__ = AsyncMock(return_value=None)
        lock_mock.return_value.__aexit__ = AsyncMock(return_value=None)
        resp = await web.client.delete("/api/hunts/U1")
    assert resp.status_code == 200
    lock_mock.assert_called_once_with([3, 1])

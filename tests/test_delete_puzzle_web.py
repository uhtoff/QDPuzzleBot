"""Entrypoint handling for the F2 target-puzzle delete guard.

The authoritative guard lives in service.delete_puzzle (raises ValueError);
these tests pin that both entrypoints surface it cleanly instead of as a
500 / generic failure:
  - web DELETE /api/puzzles/<id> -> 400 cannot_delete_target
  - !delete_puzzle -> a clear message, no generic ":x: Channel deletion failed"
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_puzzle


@asynccontextmanager
async def _noop_lock(_id):
    yield


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.delete_puzzle = AsyncMock(
        side_effect=ValueError("cannot delete a target puzzle; delete its tag group")
    )
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    pdb = MagicMock()
    pdb.get_by_attr.return_value = make_puzzle(id=7, is_target=True)
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions.puzzle_lock", _noop_lock),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_web_delete_target_returns_400(web):
    resp = await web.client.delete("/api/puzzles/7")
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "cannot_delete_target"


@pytest.mark.asyncio
async def test_bang_delete_target_sends_clear_message():
    """!delete_puzzle on a target sends a specific message, not the generic failure."""
    from bot.cogs.puzzles import Puzzles

    cog = MagicMock()
    cog.service.delete_puzzle = AsyncMock(
        side_effect=ValueError("cannot delete a target puzzle; delete its tag group")
    )
    puzzle = make_puzzle(id=7, name="Meta", is_target=True)
    cog.get_channel_type.return_value = "Puzzle"
    cog.get_puzzle.return_value = puzzle
    cog.get_hunt.return_value = make_hunt(channel_id=555)
    cog._confirm_destructive = AsyncMock(return_value=True)
    general = MagicMock()
    general.send = AsyncMock()
    cog.bot.get_channel.return_value = general

    ctx = MagicMock()
    ctx.send = AsyncMock()

    await Puzzles.delete_puzzle.callback(cog, ctx)

    sent = " ".join(
        str(c.args[0]) for c in general.send.call_args_list + ctx.send.call_args_list if c.args
    )
    assert "target" in sent.lower()
    assert "Channel deletion failed" not in sent

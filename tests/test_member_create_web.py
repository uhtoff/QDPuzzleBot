"""Member-accessible create endpoints — any guild member can create puzzles/groups.

These tests verify the organiser gate is NOT present on create_puzzle and create_meta.
Pattern mirrors tests/test_edge_web.py: minimal Quart app, patched auth helpers.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle, make_round

_ASYNC_SERVICE_METHODS = [
    "create_puzzle", "create_tag_group", "refresh_unassigned_safe", "set_source_mode",
]


def _build_cog():
    cog = MagicMock()
    svc = cog.service
    for name in _ASYNC_SERVICE_METHODS:
        setattr(svc, name, AsyncMock())
    puzzle = make_puzzle(id=9, channel_id=100)
    svc.create_puzzle = AsyncMock(return_value=puzzle)
    svc.create_tag_group = AsyncMock(
        return_value=(make_round(id=5, group_code="R1"), puzzle)
    )
    return cog, puzzle


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    cog, puzzle = _build_cog()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        # _visible_hunt_by_uid is patched (NOT _organiser_hunt) — member-level only
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb"),
        patch("bot.store.PuzzleJsonDb"),
        patch("bot.store.TagGroupJsonDb"),
    ]
    started = [p.start() for p in patches]
    _hdb, _pdb, _rdb = started[3], started[4], started[5]
    _pdb.get_by_attr.return_value = puzzle
    _pdb.check_duplicates_in_hunt.return_value = False
    _rdb.get_by_attr.return_value = None
    _hdb.get_by_attr.return_value = MagicMock()

    try:
        yield SimpleNamespace(
            app=app, client=app.test_client(), cog=cog, svc=cog.service,
            puzzle=puzzle,
        )
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_member_can_create_puzzle(web):
    """Any member (no organiser role) can POST /api/hunts/<uid>/puzzles."""
    resp = await web.client.post("/api/hunts/U1/puzzles", json={"name": "My Puzzle"})
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_member_can_create_group(web):
    """Any member (no organiser role) can POST /api/hunts/<uid>/metas."""
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "My Round"})
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["ok"] is True

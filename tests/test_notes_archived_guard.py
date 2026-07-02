"""#34: POST /puzzles/<id>/notes and DELETE /puzzles/<id>/notes/<index> were the
only puzzle-mutating web endpoints missing the archived-hunt guard every sibling
mutation enforces. Mirrors the harness in test_edge_web.py / test_evidence_web.py."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle


def _ctx(archived=False):
    return {"hunt": {"id": 10, "uid": "U1", "archived": archived}, "guild_snowflake": "123"}


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.add_note = AsyncMock()
    cog.service.erase_note = AsyncMock(return_value=True)

    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.register_blueprint(actions_bp)

    puzzle = make_puzzle(id=9, hunt_id=10)
    hdb = MagicMock()
    hdb.get_by_attr.return_value = SimpleNamespace(id=10, uid="U1")
    pdb = MagicMock()
    pdb.get_by_attr.return_value = puzzle

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(_ctx(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    started = [p.start() for p in patches]
    try:
        yield SimpleNamespace(
            app=app, client=app.test_client(), cog=cog, puzzle=puzzle, vis=started[1],
        )
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_add_note_rejects_archived_hunt(web):
    web.vis.return_value = (_ctx(archived=True), None)

    resp = await web.client.post("/api/puzzles/9/notes", json={"note": "hax"})

    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "hunt_archived"
    web.cog.service.add_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_note_succeeds_on_active_hunt(web):
    resp = await web.client.post("/api/puzzles/9/notes", json={"note": "ok"})

    assert resp.status_code == 200
    web.cog.service.add_note.assert_awaited_once()


@pytest.mark.asyncio
async def test_erase_note_rejects_archived_hunt(web):
    web.vis.return_value = (_ctx(archived=True), None)

    resp = await web.client.delete("/api/puzzles/9/notes/1")

    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "hunt_archived"
    web.cog.service.erase_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_erase_note_succeeds_on_active_hunt(web):
    resp = await web.client.delete("/api/puzzles/9/notes/1")

    assert resp.status_code == 200
    web.cog.service.erase_note.assert_awaited_once()

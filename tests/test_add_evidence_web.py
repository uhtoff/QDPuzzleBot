"""#37: POST /api/evidence was the only evidence-writing endpoint missing the
archived-hunt guard, and crashed with an unhandled AttributeError (partial
write, no refresh) if any note in the list wasn't a string."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle


def _hunt(archived=False):
    return {"id": 10, "uid": "U1", "archived": archived, "guild_id": 1}


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.api import api_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.refresh_evidence_safe = AsyncMock()
    cog.service.refresh_informs_targets = AsyncMock()
    cog.service.log_clues = AsyncMock(
        side_effect=lambda hunt, puzzle, notes: [SimpleNamespace(id=1, note=n) for n in notes]
    )
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.register_blueprint(api_bp)

    edb = MagicMock()
    edb.log.side_effect = lambda hunt_id, source_puzzle_id, note: SimpleNamespace(id=1, note=note)

    pdb = MagicMock()
    pdb.get_by_attr.return_value = make_puzzle(id=9, hunt_id=10)

    hdb = MagicMock()
    hdb.get_by_attr.return_value = SimpleNamespace(id=10)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(_hunt(), {}))),
        patch("bot.store.EvidenceJsonDb", edb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    started = [p.start() for p in patches]
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, edb=edb, vis=started[1])
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_rejects_archived_hunt(web):
    web.vis.return_value = (_hunt(archived=True), {})

    resp = await web.client.post(
        "/api/evidence", json={"hunt_uid": "U1", "source_puzzle_id": 9, "notes": ["a clue"]}
    )

    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "hunt_archived"
    web.cog.service.log_clues.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_non_string_note_without_partial_write(web):
    resp = await web.client.post(
        "/api/evidence", json={"hunt_uid": "U1", "source_puzzle_id": 9, "notes": ["ok", 42]}
    )

    assert resp.status_code == 400
    # Must not have already written "ok" before hitting the bad element.
    web.cog.service.log_clues.assert_not_awaited()


@pytest.mark.asyncio
async def test_succeeds_with_valid_notes(web):
    resp = await web.client.post(
        "/api/evidence", json={"hunt_uid": "U1", "source_puzzle_id": 9, "notes": ["a clue"]}
    )

    assert resp.status_code == 201
    web.cog.service.log_clues.assert_awaited_once()
    web.cog.service.refresh_evidence_safe.assert_awaited_once()
    web.cog.service.refresh_informs_targets.assert_awaited_once()

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
        side_effect=lambda hunt, puzzle, notes, media_id=None: [
            SimpleNamespace(id=1, note=n) for n in notes
        ]
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


@pytest.mark.asyncio
async def test_requires_login(web):
    """@login_required must actually reject an unauthenticated request, not
    just decorate the route — every other fixture in this file hands out a
    valid session, so nothing else here would catch the decorator being
    removed or miswired."""
    with patch("bot.web.auth.session", {}):
        resp = await web.client.post(
            "/api/evidence", json={"hunt_uid": "U1", "notes": ["a clue"]}
        )

    assert resp.status_code == 401
    web.cog.service.log_clues.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_archived_hunt_without_source_puzzle(web):
    """The archived-hunt guard must still bite when there is no source
    puzzle at all — it must not be reachable only via the puzzle-lookup
    branch."""
    web.vis.return_value = (_hunt(archived=True), {})

    resp = await web.client.post(
        "/api/evidence", json={"hunt_uid": "U1", "notes": ["seen on the poster"]}
    )

    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "hunt_archived"
    web.cog.service.log_clues.assert_not_awaited()


class TestSourcelessAndImageEvidence:
    async def test_accepts_evidence_with_no_source_puzzle(self, web):
        resp = await web.client.post("/api/evidence", json={
            "hunt_uid": "U1", "notes": ["seen on the poster"],
        })

        assert resp.status_code == 201
        # Pin the actual puzzle argument passed through to log_clues, not just
        # the status code -- a mutation that keeps requiring source_puzzle_id
        # internally (e.g. defaulting it to some sentinel) would still return
        # 201 here if only the status code were checked.
        args, _ = web.cog.service.log_clues.await_args
        assert args[1] is None
        # refresh_informs_targets is only fired when there IS a source puzzle.
        web.cog.service.refresh_informs_targets.assert_not_awaited()
        web.cog.service.refresh_evidence_safe.assert_awaited_once()

    async def test_accepts_an_image_with_no_note(self, web):
        resp = await web.client.post("/api/evidence", json={
            "hunt_uid": "U1", "notes": [], "media_id": 7,
        })

        assert resp.status_code == 201
        # media_id must actually reach log_clues -- a mock service call
        # returns 201 regardless of whether the kwarg is dropped on the way,
        # so the status code alone proves nothing about this.
        args, kwargs = web.cog.service.log_clues.await_args
        assert kwargs.get("media_id") == 7
        # An image with no note still needs a row: a single empty-string note,
        # not an empty list (which would silently log nothing).
        assert args[2] == [""]

    async def test_rejects_neither_note_nor_image(self, web):
        resp = await web.client.post("/api/evidence", json={
            "hunt_uid": "U1", "notes": [],
        })

        assert resp.status_code == 400
        web.cog.service.log_clues.assert_not_awaited()

    async def test_still_rejects_non_string_notes(self, web):
        resp = await web.client.post("/api/evidence", json={
            "hunt_uid": "U1", "notes": [{"not": "a string"}],
        })

        assert resp.status_code == 400
        web.cog.service.log_clues.assert_not_awaited()


class TestAddEvidenceAudit:
    """Task 17 Step 3b: moving the dashboard's clue-logging off the actions
    blueprint's /clues route (which calls _audit("clue", ...)) must not
    silently drop the audit trail. /api/evidence carries the same call."""

    async def test_audits_a_sourced_clue(self, web):
        audit = AsyncMock()
        with patch("bot.web.actions._audit", audit):
            resp = await web.client.post(
                "/api/evidence",
                json={"hunt_uid": "U1", "source_puzzle_id": 9, "notes": ["a clue"]},
            )

        assert resp.status_code == 201
        audit.assert_awaited_once_with("clue", puzzle_id=9, hunt_uid="U1")

    async def test_audits_a_sourceless_clue(self, web):
        audit = AsyncMock()
        with patch("bot.web.actions._audit", audit):
            resp = await web.client.post(
                "/api/evidence",
                json={"hunt_uid": "U1", "notes": ["seen on the poster"]},
            )

        assert resp.status_code == 201
        audit.assert_awaited_once_with("clue", puzzle_id=None, hunt_uid="U1")

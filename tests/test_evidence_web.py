"""Web-route security/correctness tests for the evidence PATCH/DELETE endpoints.

Mirrors the harness in test_edge_web.py: a minimal Quart app registering only
``actions_bp``, with auth/visibility helpers patched so the request body runs.

Covers correctness-review findings:
  F1 — evidence PATCH/DELETE must be hunt-visibility checked, must reject
       archived hunts (PATCH had no guard), and must reject a target_puzzle_id
       that belongs to a different hunt.
  F3 — evidence note-edit/target-change/delete must refresh the right target
       contribution tables and run the informs cleanup on delete.
"""
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
    svc = cog.service
    for name in (
        "link_informs_for_evidence", "unlink_informs_for_evidence",
        "refresh_target_for_puzzle", "refresh_evidence",
    ):
        setattr(svc, name, AsyncMock())
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    # Evidence item id=5 lives in hunt 10, sourced from puzzle 1.
    item = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1, target_puzzle_id=None, note="old")

    edb = MagicMock()
    edb.get_by_id.return_value = item
    edb.update_note.return_value = item
    edb.set_target.return_value = item

    hdb = MagicMock()
    hunt_obj = SimpleNamespace(id=10, uid="U1", archived=False)
    hdb.get_by_attr.return_value = hunt_obj

    pdb = MagicMock()
    # A puzzle in the SAME hunt (10); tests override for the cross-hunt case.
    pdb.get_by_attr.return_value = make_puzzle(id=9, hunt_id=10)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_evidence", AsyncMock(return_value=(_ctx(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.EvidenceJsonDb", edb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    started = [p.start() for p in patches]
    try:
        yield SimpleNamespace(
            app=app, client=app.test_client(), cog=cog, svc=svc,
            item=item, hunt_obj=hunt_obj, edb=edb, hdb=hdb, pdb=pdb,
            vis=started[1],
        )
    finally:
        for p in patches:
            p.stop()


# --- F1: visibility gate -----------------------------------------------------

@pytest.mark.asyncio
async def test_patch_forbidden_when_hunt_not_visible(web):
    web.vis.return_value = (None, ({"error": "forbidden"}, 403))

    resp = await web.client.patch("/api/evidence/5", json={"note": "hax"})

    assert resp.status_code == 403
    web.edb.update_note.assert_not_called()
    web.edb.set_target.assert_not_called()


@pytest.mark.asyncio
async def test_delete_forbidden_when_hunt_not_visible(web):
    web.vis.return_value = (None, ({"error": "forbidden"}, 403))

    resp = await web.client.delete("/api/evidence/5")

    assert resp.status_code == 403
    web.edb.delete.assert_not_called()


# --- F1: archived guard (PATCH lacked it) ------------------------------------

@pytest.mark.asyncio
async def test_patch_rejects_archived_hunt(web):
    web.vis.return_value = (_ctx(archived=True), None)

    resp = await web.client.patch("/api/evidence/5", json={"note": "edit"})

    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "hunt_archived"
    web.edb.update_note.assert_not_called()


@pytest.mark.asyncio
async def test_delete_rejects_archived_hunt(web):
    web.vis.return_value = (_ctx(archived=True), None)

    resp = await web.client.delete("/api/evidence/5")

    assert resp.status_code == 403
    web.edb.delete.assert_not_called()


# --- F1: cross-hunt target routing -------------------------------------------

@pytest.mark.asyncio
async def test_patch_rejects_target_in_other_hunt(web):
    # Target puzzle 99 belongs to a DIFFERENT hunt (77).
    web.pdb.get_by_attr.return_value = make_puzzle(id=99, hunt_id=77)

    resp = await web.client.patch("/api/evidence/5", json={"target_puzzle_id": 99})

    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "cross_hunt_target"
    web.edb.set_target.assert_not_called()

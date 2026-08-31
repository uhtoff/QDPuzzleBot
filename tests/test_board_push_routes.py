"""M31: the route-side counterpart of tests/test_board_push_service.py.

`test_board_push_service.py` pins that every board-visible PuzzleService
mutation carries `@pushes_board`. That covers nothing for the routes in
`bot/web/actions.py` that mutate through a DAO directly, or whose only push
comes from a service call they make *conditionally* — those bypass the
decorator entirely and leave a connected dashboard showing stale data until
something unrelated triggers a refresh.

Three families were in that gap and are pinned here:

  * canvas CRUD (`create_canvas` / `update_canvas` / `delete_canvas`), which
    call `CanvasJsonDb` directly while every sibling in the same section
    (`create_region`, `retile_canvas`, the markup routes, ...) pushes;
  * evidence `PATCH`/`DELETE`, which pushed only *incidentally* when an informs
    edge flipped, via the `@pushes_board`-decorated `tag_with_edge`/`untag_from`
    behind `link_informs_for_evidence_safe`/`unlink_informs_for_evidence_safe`.
    A note edit, a media swap, or the delete of an edge-less item changed
    board-visible state and pushed nothing — those are the cases asserted below;
  * `post_hunt_archive`, which commits `hunts.archived` through `HuntJsonDb`.

Every assertion here is on `events.publish` (what `_notify_board` calls), not on
the status code: a route that answers 200 and pushes nothing is exactly the bug.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)


def _media(id=7, hunt_id=10):
    return SimpleNamespace(id=id, hunt_id=hunt_id, width=1024, height=768,
                           tile_status="ready", tile_levels=3)


def _canvas(id=3, hunt_id=10, media_id=7):
    return SimpleNamespace(id=id, hunt_id=hunt_id, media_id=media_id,
                           name="Extravaganza", note=None)


def _published_board_uids(pub):
    """The uid of every board event `pub` was handed."""
    return [call.args[0]["uid"] for call in pub.call_args_list
            if call.args and call.args[0].get("type") == "board"]


# --- Canvas CRUD ------------------------------------------------------------

@pytest.fixture
def canvas_web():
    cog = MagicMock()
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()

    cdb, rdb, mdb, hdb = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    cdb.get_by_id.return_value = _canvas()
    cdb.create.return_value = _canvas()
    rdb.get_by_canvas.return_value = []
    mdb.get_by_id.return_value = _media()
    hdb.get_by_attr.return_value = make_hunt(id=10)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_canvas", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.CanvasJsonDb", cdb),
        patch("bot.store.CanvasRegionJsonDb", rdb),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions.events.publish"),
    ]
    mocks = start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), ctx=ctx,
                              cdb=cdb, mdb=mdb, pub=mocks[-1])
    finally:
        stop_patches(patches)


class TestCanvasCrudPushesBoard:
    """A canvas is board-visible state: HuntBoard.tsx invalidates
    ["canvases", uid] on every board event. Without these pushes a second
    person's canvas panel keeps showing a canvas that was deleted, or misses one
    that was just added, until they reload."""

    async def test_create_pushes(self, canvas_web):
        resp = await canvas_web.client.post("/api/hunts/U1/canvases",
                                            json={"media_id": 7, "name": "Extravaganza"})
        assert resp.status_code == 201
        canvas_web.cdb.create.assert_called_once()
        assert _published_board_uids(canvas_web.pub) == ["U1"]

    async def test_update_pushes(self, canvas_web):
        resp = await canvas_web.client.patch("/api/canvases/3", json={"name": "Renamed"})
        assert resp.status_code == 200
        canvas_web.cdb.update.assert_called_once()
        assert _published_board_uids(canvas_web.pub) == ["U1"]

    async def test_delete_pushes(self, canvas_web, tmp_path):
        with patch("bot.core.message_media.media_root", return_value=tmp_path):
            resp = await canvas_web.client.delete("/api/canvases/3")
        assert resp.status_code == 200
        canvas_web.cdb.delete.assert_called_once_with(3)
        assert _published_board_uids(canvas_web.pub) == ["U1"]

    async def test_a_refused_create_pushes_nothing(self, canvas_web):
        """The push must follow the write, not the request: a 403 changed
        nothing, so telling every dashboard to refetch is noise."""
        with patch("bot.web.actions.require_organiser",
                   AsyncMock(return_value=({"error": "forbidden"}, 403))):
            resp = await canvas_web.client.post("/api/hunts/U1/canvases",
                                                json={"media_id": 7})
        assert resp.status_code == 403
        assert _published_board_uids(canvas_web.pub) == []

    async def test_a_cross_hunt_media_create_pushes_nothing(self, canvas_web):
        canvas_web.mdb.get_by_id.return_value = _media(hunt_id=999)
        resp = await canvas_web.client.post("/api/hunts/U1/canvases",
                                            json={"media_id": 7})
        assert resp.status_code == 400
        canvas_web.cdb.create.assert_not_called()
        assert _published_board_uids(canvas_web.pub) == []


# --- Hunt archive -----------------------------------------------------------

@pytest.fixture
def hunt_web():
    cog = MagicMock()
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()

    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt(id=10, uid="U1")

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions.events.publish"),
    ]
    mocks = start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), ctx=ctx,
                              hdb=hdb, pub=mocks[-1])
    finally:
        stop_patches(patches)


class TestHuntArchivePushesBoard:
    """Archiving flips every mutation route in the hunt to 403 and the SPA
    greys the hunt out — a state change nobody else's open dashboard learns
    about without a push."""

    async def test_archiving_pushes(self, hunt_web):
        resp = await hunt_web.client.post("/api/hunts/U1/archive", json={"archived": True})
        assert resp.status_code == 200
        hunt_web.hdb.commit.assert_called_once()
        assert _published_board_uids(hunt_web.pub) == ["U1"]

    async def test_unarchiving_pushes(self, hunt_web):
        resp = await hunt_web.client.post("/api/hunts/U1/archive", json={"archived": False})
        assert resp.status_code == 200
        assert _published_board_uids(hunt_web.pub) == ["U1"]

    async def test_a_missing_hunt_pushes_nothing(self, hunt_web):
        hunt_web.hdb.get_by_attr.return_value = None
        resp = await hunt_web.client.post("/api/hunts/U1/archive", json={"archived": True})
        assert resp.status_code == 404
        assert _published_board_uids(hunt_web.pub) == []


# --- Evidence PATCH / DELETE ------------------------------------------------

@pytest.fixture
def evidence_web():
    cog = MagicMock()
    for name in ("link_informs_for_evidence_safe", "unlink_informs_for_evidence_safe",
                 "refresh_target_for_puzzle", "refresh_evidence_safe"):
        setattr(cog.service, name, AsyncMock())
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()

    # Evidence item 5: hunt 10, sourced from puzzle 1, deliberately UNTARGETED —
    # the edge-less item whose edits and deletion fire no @pushes_board service
    # call at all, so the route is the only thing that can push.
    item = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1,
                           target_puzzle_id=None, note="old", media_id=None)
    edb = MagicMock()
    edb.get_by_id.return_value = item
    edb.update_note.return_value = item
    edb.update_media.return_value = item
    edb.set_target.return_value = item

    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt(id=10, uid="U1")
    pdb = MagicMock()
    pdb.get_by_attr.return_value = make_puzzle(id=9, hunt_id=10)
    mdb = MagicMock()
    mdb.get_by_id.return_value = _media()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_evidence", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.EvidenceJsonDb", edb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.web.actions.events.publish"),
    ]
    mocks = start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), ctx=ctx, cog=cog,
                              item=item, edb=edb, vis=mocks[1], pub=mocks[-1])
    finally:
        stop_patches(patches)


class TestEvidenceWritesPushBoard:
    """The cases the `@pushes_board` decorator cannot reach.

    `update_evidence`/`delete_evidence` only ever pushed as a side effect of
    `tag_with_edge`/`untag_from` firing when an informs edge changed. Every test
    here mutates an item with NO target, so no edge moves and no decorated
    service call runs — if the route does not push, nothing does.
    """

    async def test_a_note_edit_with_no_edge_change_pushes(self, evidence_web):
        resp = await evidence_web.client.patch("/api/evidence/5", json={"note": "edited"})
        assert resp.status_code == 200
        evidence_web.edb.update_note.assert_called_once_with(5, "edited")
        # No edge moved, so tag_with_edge/untag_from never ran.
        evidence_web.cog.service.link_informs_for_evidence_safe.assert_not_called()
        assert _published_board_uids(evidence_web.pub) == ["U1"]

    async def test_a_media_swap_with_no_edge_change_pushes(self, evidence_web):
        resp = await evidence_web.client.patch("/api/evidence/5", json={"media_id": 7})
        assert resp.status_code == 200
        evidence_web.edb.update_media.assert_called_once_with(5, 7)
        assert _published_board_uids(evidence_web.pub) == ["U1"]

    async def test_deleting_an_edge_less_item_pushes(self, evidence_web):
        resp = await evidence_web.client.delete("/api/evidence/5")
        assert resp.status_code == 200
        evidence_web.edb.delete.assert_called_once_with(5)
        evidence_web.cog.service.unlink_informs_for_evidence_safe.assert_not_called()
        assert _published_board_uids(evidence_web.pub) == ["U1"]

    async def test_a_refused_patch_pushes_nothing(self, evidence_web):
        evidence_web.vis.return_value = (None, ({"error": "forbidden"}, 403))
        resp = await evidence_web.client.patch("/api/evidence/5", json={"note": "hax"})
        assert resp.status_code == 403
        assert _published_board_uids(evidence_web.pub) == []

    async def test_a_cross_hunt_target_patch_pushes_nothing(self, evidence_web):
        """The request is refused before anything is written, so there is
        nothing to tell the dashboards about."""
        with patch("bot.store.PuzzleJsonDb") as pdb:
            pdb.get_by_attr.return_value = make_puzzle(id=9, hunt_id=999)
            resp = await evidence_web.client.patch("/api/evidence/5",
                                                   json={"target_puzzle_id": 9})
        assert resp.status_code == 400
        assert _published_board_uids(evidence_web.pub) == []

    async def test_a_delete_of_a_missing_item_pushes_nothing(self, evidence_web):
        evidence_web.edb.get_by_id.return_value = None
        resp = await evidence_web.client.delete("/api/evidence/5")
        assert resp.status_code == 404
        assert _published_board_uids(evidence_web.pub) == []

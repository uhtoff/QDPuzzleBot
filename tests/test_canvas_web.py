"""Plan B2a: the canvas and region routes.

Two different permission bars, which is the thing most worth pinning here:
creating or deleting a CANVAS is organiser-level, while drawing and editing
REGIONS is open to any member who can see the hunt — the same bar as logging
evidence, because everyone marks the picture up during a hunt.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_hunt_ctx, make_web_app, start_patches, stop_patches

RECT = {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]}


def _media(id=7, hunt_id=10):
    return SimpleNamespace(id=id, hunt_id=hunt_id, width=14400, height=14400,
                           tile_status="ready", tile_levels=6)


def _canvas(id=3, hunt_id=10, media_id=7):
    return SimpleNamespace(id=id, hunt_id=hunt_id, media_id=media_id,
                           name="Extravaganza", note=None)


def _region(id=5, canvas_id=3, puzzle_id=None, role=None, label="",
            crop_media_id=None):
    return SimpleNamespace(id=id, canvas_id=canvas_id, shape=json.dumps(RECT),
                           label=label, note=None, puzzle_id=puzzle_id, role=role,
                           crop_media_id=crop_media_id)


@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
    cdb, rdb, mdb, hdb, pidb = (MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                               MagicMock())
    cdb.get_by_id.return_value = _canvas()
    cdb.create.return_value = _canvas()
    rdb.get_by_id.return_value = _region()
    rdb.get_by_canvas.return_value = [_region()]
    mdb.get_by_id.return_value = _media()
    hdb.get_by_attr.return_value = make_hunt(id=10)
    # canvas_service.{create,update}_region always call
    # puzzle_images.sync_region_image; without this it hits the real DAO and a
    # real DB, whether or not the test cares about puzzle images.
    pidb.get_by_region.return_value = None
    pidb.get_by_puzzle.return_value = []
    # Linking a region now looks its target up to prove it belongs to this
    # hunt; without a stand-in every linked-region test would reach the real
    # DAO and be refused as cross-hunt.
    pzdb = MagicMock()
    pzdb.get_by_attr.return_value = SimpleNamespace(id=9, hunt_id=10)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_canvas",
              AsyncMock(return_value=(ctx, None))),
        # Organiser by default; individual tests override to prove the bar.
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.CanvasJsonDb", cdb),
        patch("bot.store.CanvasRegionJsonDb", rdb),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pzdb),
        patch("bot.core.puzzle_images.PuzzleImageJsonDb", pidb),
        # Phase 4: _push_board was replaced by _notify_board, which publishes
        # straight to events.publish rather than through cog.service.notifier
        # -- the board-push assertion below patches this instead of the cog.
        patch("bot.web.actions.events.publish"),
    ]
    mocks = start_patches(patches)
    pub = mocks[-1]
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx,
                             cdb=cdb, rdb=rdb, mdb=mdb, pidb=pidb, pzdb=pzdb,
                             pub=pub)
    finally:
        stop_patches(patches)


class TestCanvasCreate:
    async def test_creates_a_canvas(self, web):
        resp = await web.client.post("/api/hunts/U1/canvases",
                                     json={"media_id": 7, "name": "Extravaganza"})
        assert resp.status_code == 201
        body = await resp.get_json()
        assert body["media_id"] == 7
        # The viewer needs these before it fetches a single tile.
        assert (body["width"], body["height"]) == (14400, 14400)
        assert body["tile_status"] == "ready"

    async def test_requires_organiser(self, web):
        """Uploading a shared image everyone works on is organiser-level, unlike
        drawing on one."""
        # A plain (dict, status) tuple, not jsonify(...): building a response
        # object here would need an app context the fixture does not push.
        with patch("bot.web.actions.require_organiser",
                   AsyncMock(return_value=({"error": "forbidden"}, 403))):
            resp = await web.client.post("/api/hunts/U1/canvases",
                                         json={"media_id": 7})
        assert resp.status_code == 403

    async def test_rejects_a_non_numeric_media_id_as_a_client_error(self, web):
        """The id is cast with int() before anything validates it, so a
        non-numeric one raised ValueError out of the route -- a 500 that reads
        as a server fault for what is plainly a bad request."""
        resp = await web.client.post("/api/hunts/U1/canvases",
                                     json={"media_id": "not-a-number"})
        assert resp.status_code == 400

    async def test_rejects_media_from_another_hunt(self, web):
        """Otherwise the tile route would serve another hunt's bytes to anyone who
        can see this one."""
        web.mdb.get_by_id.return_value = _media(hunt_id=99)
        resp = await web.client.post("/api/hunts/U1/canvases", json={"media_id": 7})
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "cross_hunt_media"

    async def test_rejects_a_missing_media_id(self, web):
        resp = await web.client.post("/api/hunts/U1/canvases", json={})
        assert resp.status_code == 400

    async def test_rejects_an_archived_hunt(self, web):
        web.ctx["hunt"]["archived"] = True
        resp = await web.client.post("/api/hunts/U1/canvases", json={"media_id": 7})
        assert resp.status_code == 403

    async def test_unauthenticated_request_is_401(self, web):
        with patch("bot.web.auth.session", {}):
            resp = await web.client.post("/api/hunts/U1/canvases",
                                         json={"media_id": 7})
        assert resp.status_code == 401


class TestCanvasDelete:
    async def test_removes_the_tile_directory(self, web, tmp_path):
        """B1 left tile sweeping to the first delete path. Without this, deleting
        canvases leaks a 9.6 MB pyramid every time."""
        tiles = tmp_path / "tiles" / "10" / "7"
        tiles.mkdir(parents=True)
        (tiles / "0").mkdir()
        with patch("bot.core.message_media.media_root", return_value=tmp_path):
            resp = await web.client.delete("/api/canvases/3")
        assert resp.status_code == 200
        assert not tiles.exists()
        web.cdb.delete.assert_called_once_with(3)

    async def test_requires_organiser(self, web):
        # A plain (dict, status) tuple, not jsonify(...): building a response
        # object here would need an app context the fixture does not push.
        with patch("bot.web.actions.require_organiser",
                   AsyncMock(return_value=({"error": "forbidden"}, 403))):
            resp = await web.client.delete("/api/canvases/3")
        assert resp.status_code == 403
        web.cdb.delete.assert_not_called()

    async def test_deleting_a_canvas_resets_the_media_so_it_can_be_retiled(self, web, tmp_path):
        """C4: without this reset, the rmtree above removes the pyramid but the
        media row keeps believing tile_status='ready' with its old tile_levels.
        retile_canvas treats 'ready' as nothing to do and refuses with 409, so
        the only way back was a manual DB edit."""
        tiles = tmp_path / "tiles" / "10" / "7"
        tiles.mkdir(parents=True)
        with patch("bot.core.message_media.media_root", return_value=tmp_path):
            resp = await web.client.delete("/api/canvases/3")
        assert resp.status_code == 200
        web.mdb.update_tiles.assert_called_once_with(7, "pending", 0)

    async def test_a_canvas_recreated_from_the_same_media_can_be_retiled(self, web, tmp_path):
        """The flow C4 is actually about: delete a canvas, then build a new one
        from the same uploaded image. Before the fix the media row stayed
        'ready' with no pyramid on disk, so retile_canvas's state machine
        believed it was already fine and refused with 409 forever.

        Driven through the real delete and retile routes: the fixture's
        mdb.get_by_id always returns the same SimpleNamespace, so wiring
        update_tiles' side effect to mutate it in place lets that one mock
        stand in for the DB row across both requests.
        """
        media = web.mdb.get_by_id.return_value
        assert media.tile_status == "ready"

        def _apply_update(media_id, tile_status, tile_levels=0):
            media.tile_status = tile_status
            media.tile_levels = tile_levels
        web.mdb.update_tiles.side_effect = _apply_update

        tiles = tmp_path / "tiles" / "10" / "7"
        tiles.mkdir(parents=True)
        with patch("bot.core.message_media.media_root", return_value=tmp_path):
            del_resp = await web.client.delete("/api/canvases/3")
        assert del_resp.status_code == 200

        with patch("bot.web.actions.schedule_tiling") as tile:
            retile_resp = await web.client.post("/api/canvases/3/retile")
        assert retile_resp.status_code == 202
        assert tile.called


class TestRegionWrites:
    async def test_a_non_organiser_can_draw(self, web):
        """The deliberate difference from canvas creation: everyone marks the
        picture up during a hunt, so this must NOT be organiser-gated."""
        # A plain (dict, status) tuple, not jsonify(...): building a response
        # object here would need an app context the fixture does not push.
        with patch("bot.web.actions.require_organiser",
                   AsyncMock(return_value=({"error": "forbidden"}, 403))), \
             patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"):
            rdb.create.return_value = _region()
            resp = await web.client.post("/api/canvases/3/regions",
                                         json={"shape": RECT})
        assert resp.status_code == 201

    async def test_created_region_is_returned_with_a_parsed_shape(self, web):
        with patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"):
            rdb.create.return_value = _region()
            resp = await web.client.post("/api/canvases/3/regions",
                                         json={"shape": RECT})
        body = await resp.get_json()
        # An object, not a JSON string: the viewer never double-decodes.
        assert body["shape"] == RECT

    async def test_malformed_geometry_is_a_400_with_a_message(self, web):
        resp = await web.client.post(
            "/api/canvases/3/regions",
            json={"shape": {"type": "rect", "points": [[0.5, 0.1], [0.5, 0.9]]}})
        assert resp.status_code == 400
        assert "width" in (await resp.get_json())["error"]

    async def test_a_half_set_link_is_a_400(self, web):
        resp = await web.client.post("/api/canvases/3/regions",
                                     json={"shape": RECT, "role": "informs"})
        assert resp.status_code == 400

    async def test_drawing_pushes_the_board(self, web):
        """So a second person annotating sees rectangles appear without a reload."""
        with patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"):
            rdb.create.return_value = _region()
            await web.client.post("/api/canvases/3/regions", json={"shape": RECT})
        web.pub.assert_called_once()

    async def test_drawing_on_an_archived_hunt_is_403(self, web):
        web.ctx["hunt"]["archived"] = True
        resp = await web.client.post("/api/canvases/3/regions", json={"shape": RECT})
        assert resp.status_code == 403

    async def test_patching_a_region_from_another_canvas_is_404(self, web):
        """Without the parent check, a region id belonging to another hunt's
        canvas would be edited under this hunt's visibility check."""
        web.rdb.get_by_id.return_value = _region(canvas_id=999)
        resp = await web.client.patch("/api/canvases/3/regions/5",
                                      json={"label": "x"})
        assert resp.status_code == 404

    async def test_patch_keeps_unsent_fields(self, web):
        """A PATCH that sends only a label must not wipe the geometry or the link."""
        web.rdb.get_by_id.return_value = _region(puzzle_id=9, role="locates")
        with patch("bot.core.canvas.update_region") as upd:
            upd.return_value = _region(puzzle_id=9, role="locates", label="named")
            resp = await web.client.patch("/api/canvases/3/regions/5",
                                          json={"label": "named"})
        assert resp.status_code == 200
        kwargs = upd.call_args.kwargs
        assert kwargs["shape"] == RECT
        assert kwargs["puzzle_id"] == 9
        assert kwargs["role"] == "locates"

    async def test_deleting_a_region(self, web):
        with patch("bot.core.canvas.CanvasRegionJsonDb") as rdb:
            resp = await web.client.delete("/api/canvases/3/regions/5")
            assert resp.status_code == 200
            rdb.delete.assert_called_once_with(5)

    async def test_unauthenticated_region_write_is_401(self, web):
        with patch("bot.web.auth.session", {}):
            resp = await web.client.post("/api/canvases/3/regions",
                                         json={"shape": RECT})
        assert resp.status_code == 401


class TestRegionCrossHuntPuzzle:
    """A region's puzzle_id comes straight off the request body, so it must be
    checked against the canvas's own hunt -- the same object-scoping bar the
    media ids on these routes already meet. An informs link writes a real
    evidence row against its target, so an unchecked id lands a contribution in
    ANOTHER hunt's puzzle."""

    async def test_creating_with_a_puzzle_from_another_hunt_is_400(self, web):
        pdb = MagicMock()
        pdb.get_by_attr.return_value = SimpleNamespace(id=9, hunt_id=99)
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb") as edb, \
             patch("bot.web.actions._crop_region_media", AsyncMock()) as crop:
            # A usable return value, so an unguarded route succeeds cleanly
            # rather than dying on serialisation -- the failure this pins is
            # the write landing, not a 500.
            rdb.create.return_value = _region(puzzle_id=9, role="informs")
            resp = await web.client.post(
                "/api/canvases/3/regions",
                json={"shape": RECT, "puzzle_id": 9, "role": "informs"})
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "cross_hunt_puzzle"
        # Refused, not merely reported: no region row, no evidence pointing
        # into hunt 99, and no crop decoded for a write that never lands.
        rdb.create.assert_not_called()
        edb.log.assert_not_called()
        edb.set_target.assert_not_called()
        crop.assert_not_awaited()

    async def test_creating_with_an_unknown_puzzle_is_400(self, web):
        pdb = MagicMock()
        pdb.get_by_attr.return_value = None
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"), \
             patch("bot.web.actions._crop_region_media", AsyncMock()):
            rdb.create.return_value = _region(puzzle_id=9, role="locates")
            resp = await web.client.post(
                "/api/canvases/3/regions",
                json={"shape": RECT, "puzzle_id": 9, "role": "locates"})
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "cross_hunt_puzzle"
        rdb.create.assert_not_called()

    async def test_creating_with_a_puzzle_from_this_hunt_still_works(self, web):
        """The positive control: this is an object-scoping check, not a new
        permission bar -- linking within the hunt must be unaffected."""
        pdb = MagicMock()
        pdb.get_by_attr.return_value = SimpleNamespace(id=9, hunt_id=10)
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"), \
             patch("bot.web.actions._crop_region_media", AsyncMock(return_value=99)):
            rdb.create.return_value = _region(puzzle_id=9, role="informs")
            resp = await web.client.post(
                "/api/canvases/3/regions",
                json={"shape": RECT, "puzzle_id": 9, "role": "informs"})
        assert resp.status_code == 201
        rdb.create.assert_called_once()

    async def test_patching_to_a_puzzle_from_another_hunt_is_400(self, web):
        pdb = MagicMock()
        pdb.get_by_attr.return_value = SimpleNamespace(id=9, hunt_id=99)
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb") as edb, \
             patch("bot.web.actions._crop_region_media", AsyncMock()) as crop:
            # First read is the pre-edit row (unlinked, so the link CHANGES and
            # the evidence row would be written); second is what the route
            # serialises back.
            rdb.get_by_id.side_effect = [_region(),
                                         _region(puzzle_id=9, role="informs")]
            resp = await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": 9, "role": "informs"})
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "cross_hunt_puzzle"
        rdb.update.assert_not_called()
        edb.log.assert_not_called()
        edb.set_target.assert_not_called()
        crop.assert_not_awaited()

    async def test_patching_within_the_hunt_still_works(self, web):
        pdb = MagicMock()
        pdb.get_by_attr.return_value = SimpleNamespace(id=9, hunt_id=10)
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media", AsyncMock(return_value=77)):
            upd.return_value = _region(puzzle_id=9, role="informs")
            resp = await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": 9, "role": "informs"})
        assert resp.status_code == 200
        assert upd.call_args.kwargs["puzzle_id"] == 9

    async def test_patching_an_unlinked_field_does_not_need_a_puzzle_lookup(self, web):
        """An unlinked region PATCHed for its label carries no puzzle_id, so
        the check must not turn a plain edit into a 400."""
        pdb = MagicMock()
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.core.canvas.update_region") as upd:
            upd.return_value = _region(label="named")
            resp = await web.client.patch("/api/canvases/3/regions/5",
                                          json={"label": "named"})
        assert resp.status_code == 200
        pdb.get_by_attr.assert_not_called()


class TestRegionCropEvidence:
    """An informs-region's evidence should carry the actual cropped image, not
    just a text reference -- so linking one crops the canvas's original image
    and wires the resulting media id through to the service call."""

    async def test_creating_an_informs_region_crops_the_canvas_image(self, web):
        with patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"), \
             patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=99)) as crop:
            rdb.create.return_value = _region(puzzle_id=9, role="informs")
            resp = await web.client.post(
                "/api/canvases/3/regions",
                json={"shape": RECT, "puzzle_id": 9, "role": "informs"})
        assert resp.status_code == 201
        crop.assert_awaited_once_with(web.cdb.get_by_id.return_value, RECT)

    async def test_the_cropped_media_id_reaches_the_service(self, web):
        with patch("bot.core.canvas.create_region") as create, \
             patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=99)):
            create.return_value = _region(puzzle_id=9, role="informs")
            await web.client.post(
                "/api/canvases/3/regions",
                json={"shape": RECT, "puzzle_id": 9, "role": "informs"})
        assert create.call_args.kwargs["media_id"] == 99

    async def test_creating_a_locates_region_crops(self, web):
        """A locates region IS the puzzle image -- without a crop it produces a
        puzzle image row with nothing to show."""
        with patch("bot.core.canvas.CanvasRegionJsonDb") as rdb, \
             patch("bot.core.canvas.EvidenceJsonDb"), \
             patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=99)) as crop:
            rdb.create.return_value = _region(puzzle_id=9, role="locates")
            resp = await web.client.post(
                "/api/canvases/3/regions",
                json={"shape": RECT, "puzzle_id": 9, "role": "locates"})
        assert resp.status_code == 201
        crop.assert_awaited_once_with(web.cdb.get_by_id.return_value, RECT)

    async def test_a_half_set_informs_link_does_not_crop(self, web):
        """This request is about to 400 for a half-set link -- cropping first
        would waste a decode on a write that never lands."""
        with patch("bot.web.actions._crop_region_media", AsyncMock()) as crop:
            resp = await web.client.post(
                "/api/canvases/3/regions", json={"shape": RECT, "role": "informs"})
        assert resp.status_code == 400
        crop.assert_not_awaited()

    async def test_relinking_to_informs_crops(self, web):
        web.rdb.get_by_id.return_value = _region(puzzle_id=9, role="locates")
        with patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=77)) as crop:
            upd.return_value = _region(puzzle_id=9, role="informs")
            await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": 9, "role": "informs"})
        crop.assert_awaited_once()
        assert upd.call_args.kwargs["media_id"] == 77

    async def test_editing_an_unchanged_informs_region_does_not_recrop(self, web):
        """Only a link CHANGE into informs should pay for a crop -- editing the
        label or geometry of an already-informs region must not redo it."""
        web.rdb.get_by_id.return_value = _region(puzzle_id=9, role="informs")
        with patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media", AsyncMock()) as crop:
            upd.return_value = _region(puzzle_id=9, role="informs", label="moved")
            await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"label": "moved", "puzzle_id": 9, "role": "informs"})
        crop.assert_not_awaited()

    async def test_dropping_the_link_does_not_crop(self, web):
        web.rdb.get_by_id.return_value = _region(puzzle_id=9, role="informs")
        with patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media", AsyncMock()) as crop:
            upd.return_value = _region()
            await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": None, "role": None})
        crop.assert_not_awaited()

    async def test_relinking_to_locates_crops(self, web):
        """A locates link is what a puzzle image points at -- without a crop it
        produces an image row with nothing to show."""
        web.rdb.get_by_id.return_value = _region(puzzle_id=None, role=None)
        with patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=88)) as crop:
            upd.return_value = _region(puzzle_id=9, role="locates")
            await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": 9, "role": "locates"})
        crop.assert_awaited_once()
        assert upd.call_args.kwargs["media_id"] == 88

    async def test_relinking_to_locates_with_inked_existing_crop_does_not_recrop(
            self, web):
        """Ink is drawn on one specific image -- regenerating the crop under it
        would leave every mark pointing at nothing it was drawn against."""
        web.rdb.get_by_id.return_value = _region(
            puzzle_id=None, role=None, crop_media_id=61)
        with patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media", AsyncMock()) as crop, \
             patch("bot.core.markup.list_marks", return_value=[SimpleNamespace(id=1)]):
            upd.return_value = _region(puzzle_id=9, role="locates",
                                       crop_media_id=61)
            await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": 9, "role": "locates"})
        crop.assert_not_awaited()
        assert upd.call_args.kwargs["media_id"] == 61

    async def test_relinking_to_locates_with_uninked_existing_crop_recrops(
            self, web):
        """Paired with the inked-crop test above: without this pairing, a guard
        that never re-crops would pass the previous test alone."""
        web.rdb.get_by_id.return_value = _region(
            puzzle_id=None, role=None, crop_media_id=61)
        with patch("bot.core.canvas.update_region") as upd, \
             patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=88)) as crop, \
             patch("bot.core.markup.list_marks", return_value=[]):
            upd.return_value = _region(puzzle_id=9, role="locates",
                                       crop_media_id=88)
            await web.client.patch(
                "/api/canvases/3/regions/5",
                json={"puzzle_id": 9, "role": "locates"})
        crop.assert_awaited_once()
        assert upd.call_args.kwargs["media_id"] == 88


class TestCreatePuzzleFromRegion:
    """Draw-to-create. Delegates to the SAME create path the board's create
    button uses, then links the region to what it returns."""

    @pytest.fixture
    def create(self, web):
        puzzle = SimpleNamespace(id=42, name="Hex maze")
        web.cog.service.create_puzzle = AsyncMock(return_value=puzzle)
        web.cog.service.refresh_unassigned_safe = AsyncMock()
        group = SimpleNamespace(id=2, hunt_id=10)
        pdb, gdb = MagicMock(), MagicMock()
        pdb.check_duplicates_in_hunt.return_value = False
        gdb.get_by_attr.return_value = group
        with patch("bot.store.PuzzleJsonDb", pdb), \
             patch("bot.store.TagGroupJsonDb", gdb), \
             patch("bot.web.actions.bot_app") as ba, \
             patch("bot.core.canvas.update_region") as upd:
            ba.return_value.bot.get_guild.return_value = MagicMock()
            upd.return_value = _region(puzzle_id=42, role="locates")
            yield SimpleNamespace(puzzle=puzzle, pdb=pdb, gdb=gdb, upd=upd)

    async def test_delegates_to_the_existing_create_path(self, web, create):
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        assert resp.status_code == 201
        web.cog.service.create_puzzle.assert_awaited_once()
        args = web.cog.service.create_puzzle.await_args[0]
        # (guild, hunt, target_group, name) — the group is passed through, so the
        # new puzzle lands in the chosen round rather than unassigned.
        assert args[3] == "Hex maze"
        assert args[2] is create.gdb.get_by_attr.return_value

    async def test_an_omitted_round_creates_an_unattached_puzzle(self, web, create):
        """The frontend's "Unassigned" option sends no round_id at all — this
        must reach create_puzzle as target_group=None, not fail or default to
        some round, matching how the board's own create control works."""
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "role": "locates"})
        assert resp.status_code == 201
        args = web.cog.service.create_puzzle.await_args[0]
        assert args[2] is None
        create.gdb.get_by_attr.assert_not_called()

    async def test_an_omitted_role_defaults_to_informs(self, web, create):
        """Nothing covered the default in either direction before. It matters:
        a locates region is geometry only — no crop, no evidence row, nothing
        the puzzle can see — so defaulting to it silently discarded the part of
        the picture the user had just drawn a box around."""
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2})
        assert resp.status_code == 201
        assert create.upd.call_args.kwargs["role"] == "informs"

    async def test_an_explicit_locates_is_still_honoured(self, web, create):
        """Paired with the default test above: a version that hard-coded
        informs, rather than defaulting to it, would pass that one alone."""
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        assert resp.status_code == 201
        assert create.upd.call_args.kwargs["role"] == "locates"

    async def test_links_the_region_to_the_new_puzzle(self, web, create):
        await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "informs"})
        kwargs = create.upd.call_args.kwargs
        assert kwargs["puzzle_id"] == 42
        assert kwargs["role"] == "informs"
        assert kwargs["region_id"] == 5

    async def test_does_not_link_when_creation_fails(self, web, create):
        """A failed create must leave the region exactly as it was, not pointing
        at a puzzle that was never made."""
        web.cog.service.create_puzzle = AsyncMock(side_effect=RuntimeError("no room"))
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        assert resp.status_code == 400
        create.upd.assert_not_called()

    async def test_rejects_a_round_from_another_hunt(self, web, create):
        create.gdb.get_by_attr.return_value = SimpleNamespace(id=2, hunt_id=99)
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "bad_round"

    async def test_rejects_a_duplicate_name(self, web, create):
        create.pdb.check_duplicates_in_hunt.return_value = True
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        assert resp.status_code == 409
        web.cog.service.create_puzzle.assert_not_awaited()

    async def test_rejects_an_empty_name(self, web, create):
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "  ", "round_id": 2, "role": "locates"})
        assert resp.status_code == 400

    async def test_rejects_a_non_numeric_round_id_as_a_client_error(self, web, create):
        """Same shape as the media_id cast: int(body["round_id"]) threw a
        ValueError straight out of the route rather than answering 400."""
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": "banana", "role": "locates"})
        assert resp.status_code == 400
        web.cog.service.create_puzzle.assert_not_awaited()

    async def test_rejects_an_unknown_role(self, web, create):
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "banana"})
        assert resp.status_code == 400
        web.cog.service.create_puzzle.assert_not_awaited()

    async def test_rejects_a_region_from_another_canvas(self, web, create):
        web.rdb.get_by_id.return_value = _region(canvas_id=999)
        resp = await web.client.post(
            "/api/canvases/3/regions/5/create-puzzle",
            json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        assert resp.status_code == 404

    async def test_informs_crops_the_region(self, web, create):
        with patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=55)) as crop:
            await web.client.post(
                "/api/canvases/3/regions/5/create-puzzle",
                json={"name": "Hex maze", "round_id": 2, "role": "informs"})
        crop.assert_awaited_once()
        assert create.upd.call_args.kwargs["media_id"] == 55

    async def test_locates_crops(self, web, create):
        """locates means the region IS the puzzle image -- without a crop it
        produces a puzzle image row with nothing to show."""
        with patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=55)) as crop:
            await web.client.post(
                "/api/canvases/3/regions/5/create-puzzle",
                json={"name": "Hex maze", "round_id": 2, "role": "locates"})
        crop.assert_awaited_once()
        assert create.upd.call_args.kwargs["media_id"] == 55


    async def test_promoting_a_region_with_an_inked_crop_does_not_recrop(
            self, web, create):
        """M11. Promoting a region to a puzzle performs the same link change as
        update_region, through the same canvas_service.update_region -- but
        without update_region's guard it regenerated the crop unconditionally.
        The old media row is then swept by the GC 24h later and its
        media_markup rows CASCADE, so a member's annotations are destroyed
        silently and permanently.
        """
        web.rdb.get_by_id.return_value = _region(
            puzzle_id=None, role=None, crop_media_id=61)
        with patch("bot.web.actions._crop_region_media", AsyncMock()) as crop, \
             patch("bot.core.markup.list_marks", return_value=[SimpleNamespace(id=1)]):
            await web.client.post(
                "/api/canvases/3/regions/5/create-puzzle",
                json={"name": "Hex maze", "round_id": 2, "role": "locates"})

        crop.assert_not_awaited()
        assert create.upd.call_args.kwargs["media_id"] == 61

    async def test_promoting_a_region_with_an_uninked_crop_still_recrops(
            self, web, create):
        """Paired with the test above: a guard that never re-crops would pass
        that one alone, and silently stop producing puzzle images."""
        web.rdb.get_by_id.return_value = _region(
            puzzle_id=None, role=None, crop_media_id=61)
        with patch("bot.web.actions._crop_region_media",
                   AsyncMock(return_value=55)) as crop, \
             patch("bot.core.markup.list_marks", return_value=[]):
            await web.client.post(
                "/api/canvases/3/regions/5/create-puzzle",
                json={"name": "Hex maze", "round_id": 2, "role": "locates"})

        crop.assert_awaited_once()
        assert create.upd.call_args.kwargs["media_id"] == 55


class TestCropRegionMedia:
    """The crop-and-save helper itself: the canvas's ORIGINAL (untiled) source
    file in, a new standalone media row out."""

    async def test_crops_the_original_and_saves_a_new_media_row(self):
        # Dimensions/tile_status are now decided inside ingest_image_bytes
        # (covered by tests/test_media_ingest.py); this test pins the wiring
        # around it -- the cropped bytes and the right hunt_id/origin reach
        # the shared ingest seam, and its returned id is what the caller gets.
        from bot.web.actions import _crop_region_media
        canvas_row = SimpleNamespace(media_id=7)
        media = SimpleNamespace(id=7, hunt_id=10, path="uploads/10/orig.png")
        mdb = MagicMock()
        mdb.get_by_id.return_value = media
        with patch("bot.store.MediaJsonDb", mdb), \
             patch("bot.core.image_tiler.crop_bytes",
                   return_value=(b"PNGDATA", 40, 30)) as crop_bytes, \
             patch("bot.web.actions.ingest_image_bytes",
                   return_value=SimpleNamespace(id=88, tile_status="none")) as ingest:
            media_id = await _crop_region_media(canvas_row, RECT)
        assert media_id == 88
        # The ORIGINAL upload, not a tile: the pyramid isn't a single
        # re-openable image.
        assert crop_bytes.call_args.args[0].name == "orig.png"
        # enforce_size_limit=False: these are bytes the server generated, so the
        # upload byte cap (a hostile-input control) must not apply — a large
        # region re-encodes past it and the crop was silently dropped.
        ingest.assert_called_once_with(
            hunt_id=10, filename="region-crop.png", raw=b"PNGDATA",
            origin="canvas_crop", db=mdb, enforce_size_limit=False)

    async def test_returns_none_when_the_canvas_media_row_is_missing(self):
        from bot.web.actions import _crop_region_media
        mdb = MagicMock()
        mdb.get_by_id.return_value = None
        with patch("bot.store.MediaJsonDb", mdb):
            assert await _crop_region_media(SimpleNamespace(media_id=7), RECT) is None

    async def test_never_raises_when_the_crop_itself_fails(self):
        """A crop is a convenience layered on the evidence row, not a
        precondition for it -- the same 'never raises' contract as
        media_tiling.tile_media, so a bad source image degrades to a
        text-only evidence row rather than blocking the region write."""
        from bot.web.actions import _crop_region_media
        media = SimpleNamespace(id=7, hunt_id=10, path="uploads/10/orig.png")
        mdb = MagicMock()
        mdb.get_by_id.return_value = media
        with patch("bot.store.MediaJsonDb", mdb), \
             patch("bot.core.image_tiler.crop_bytes",
                   side_effect=RuntimeError("boom")):
            result = await _crop_region_media(SimpleNamespace(media_id=7), RECT)
        assert result is None


class TestCanvasOcrPreview:
    """POST /api/canvases/<id>/ocr-preview -- extracts text from a rect without
    saving anything. Same permission bar as drawing a region (not organiser-
    gated), unlike the canvas create/delete routes."""

    async def test_extracts_text_from_the_crop(self, web):
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        with patch("bot.core.image_tiler.crop_bytes",
                   return_value=(b"PNGBYTES", 40, 30)), \
             patch("bot.core.ocr.extract_text", return_value="HYDROGEN") as extract:
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 200
        assert (await resp.get_json())["text"] == "HYDROGEN"
        extract.assert_called_once_with(b"PNGBYTES")

    async def test_the_crop_and_ocr_run_under_the_shared_image_bound(self, web):
        """M10. Cropping a region decodes the whole sheet, so this contends for
        exactly the memory MAX_CONCURRENT_TILING exists to cap -- but the route
        used a bare run_in_executor, and the default executor allows
        min(32, cpus+4) workers. A handful of concurrent OCR clicks could peak
        multi-GB on the Plesk box.

        This pins that the seam is used; the semaphore's own behaviour is
        media_tiling's to guarantee.
        """
        from unittest.mock import AsyncMock
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        bounded = AsyncMock(return_value="HYDROGEN")
        with patch("bot.web.actions.run_bounded_image_work", bounded):
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})

        assert resp.status_code == 200
        assert (await resp.get_json())["text"] == "HYDROGEN"
        bounded.assert_awaited_once()
        from bot.web.actions import _crop_and_extract_text
        assert bounded.await_args.args[0] is _crop_and_extract_text

    async def test_malformed_shape_is_a_400(self, web):
        resp = await web.client.post(
            "/api/canvases/3/ocr-preview",
            json={"shape": {"type": "rect", "points": [[0.5, 0.1], [0.5, 0.9]]}})
        assert resp.status_code == 400

    async def test_extraction_failure_is_a_500_not_a_crash(self, web):
        """Unlike _crop_region_media, this route must NOT swallow the error --
        the whole point of the request is giving the user something back."""
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        with patch("bot.core.image_tiler.crop_bytes", side_effect=RuntimeError("boom")):
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 500
        assert (await resp.get_json())["error"] == "ocr_failed"

    async def test_nothing_is_persisted(self, web):
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        with patch("bot.core.image_tiler.crop_bytes",
                   return_value=(b"PNGBYTES", 40, 30)), \
             patch("bot.core.ocr.extract_text", return_value="text"):
            await web.client.post("/api/canvases/3/ocr-preview", json={"shape": RECT})
        web.mdb.create.assert_not_called()

    async def test_an_archived_hunt_is_403(self, web):
        web.ctx["hunt"]["archived"] = True
        resp = await web.client.post("/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 403

    async def test_unauthenticated_is_401(self, web):
        with patch("bot.web.auth.session", {}):
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 401


class TestCanvasRetile:
    """POST /api/canvases/<id>/retile -- re-run tiling for a canvas whose
    pyramid never finished. Same permission bar as ocr-preview (not organiser-
    gated): a canvas stuck on "Preparing image" blocks everyone hunting, and
    retiling changes no content.

    'pending' is retileable as well as 'failed' because the status is only ever
    cleared by the in-process task that set it -- if the process died mid-build
    the row stays pending forever with nothing left to finish it.
    """

    def _stuck(self, web, status):
        media = SimpleNamespace(id=7, hunt_id=10, path="uploads/10/orig.png",
                                width=14400, height=14400, tile_status=status,
                                tile_levels=0)
        web.mdb.get_by_id.return_value = media
        return media

    async def test_a_failed_canvas_is_reset_to_pending_and_retiled(self, web):
        media = self._stuck(web, "failed")
        with patch("bot.web.actions.schedule_tiling") as tile:
            resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 202
        assert (await resp.get_json())["tile_status"] == "pending"
        web.mdb.update_tiles.assert_called_once_with(7, "pending", 0)
        assert tile.call_args.args[0] is media

    async def test_a_pending_canvas_is_retileable_too(self, web):
        self._stuck(web, "pending")
        with patch("bot.web.actions.schedule_tiling") as tile:
            resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 202
        web.mdb.update_tiles.assert_called_once_with(7, "pending", 0)
        assert tile.called

    async def test_a_ready_canvas_is_a_409(self, web):
        """Retiling a good pyramid is pure cost -- seconds of CPU and ~360 MB
        to rebuild what is already on disk."""
        self._stuck(web, "ready")
        with patch("bot.web.actions.schedule_tiling") as tile:
            resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 409
        assert (await resp.get_json())["error"] == "not_retileable"
        web.mdb.update_tiles.assert_not_called()
        assert not tile.called

    async def test_an_untiled_canvas_is_a_409(self, web):
        """tile_status 'none' means the image was never a tiling candidate, not
        that tiling broke; it is served whole and there is nothing to rebuild."""
        self._stuck(web, "none")
        with patch("bot.web.actions.schedule_tiling"):
            resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 409
        assert (await resp.get_json())["error"] == "not_retileable"
        web.mdb.update_tiles.assert_not_called()

    async def test_an_unknown_canvas_is_a_404(self, web):
        web.cdb.get_by_id.return_value = None
        resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 404
        web.mdb.update_tiles.assert_not_called()

    async def test_an_archived_hunt_is_403(self, web):
        web.ctx["hunt"]["archived"] = True
        resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 403
        web.mdb.update_tiles.assert_not_called()

    async def test_unauthenticated_is_401(self, web):
        with patch("bot.web.auth.session", {}):
            resp = await web.client.post("/api/canvases/3/retile")
        assert resp.status_code == 401

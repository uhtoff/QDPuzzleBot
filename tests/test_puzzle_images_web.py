"""The puzzle-images routes: a puzzle's ordered strip of images.

Same permission bar as notes/evidence -- any member who can see an unarchived
hunt, not organiser-gated -- reusing _visible_hunt_for_puzzle like the other
puzzle routes in bot/web/actions.py.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt

from bot.web import data as web_data


def _image(id=1, puzzle_id=20, media_id=7, source_region_id=None, position=0):
    return SimpleNamespace(id=id, puzzle_id=puzzle_id, media_id=media_id,
                           source_region_id=source_region_id, position=position,
                           created_at=None)


def _puzzle(id=20, hunt_id=10):
    return SimpleNamespace(id=id, hunt_id=hunt_id)


def _media(id=7, hunt_id=10):
    return SimpleNamespace(id=id, hunt_id=hunt_id)


def test_a_puzzle_image_serialises_its_media_url():
    """media_url is built by string interpolation from the id, the same shape
    get_evidence uses -- no media row lookup on this path."""
    out = web_data.puzzle_image_to_json(_image(id=1, media_id=7, position=2))
    assert out == {
        "id": 1, "media_id": 7, "media_url": "/api/media/7",
        "source_region_id": None, "position": 2,
    }


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    cog = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    pdb, mdb, hdb, pidb = (MagicMock(), MagicMock(), MagicMock(), MagicMock())
    pdb.get_by_attr.return_value = _puzzle()
    mdb.get_by_id.return_value = _media()
    hdb.get_by_attr.return_value = make_hunt(id=10)
    pidb.get_by_puzzle.return_value = [_image()]
    pidb.get_by_id.return_value = _image()
    pidb.create.return_value = _image()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        # puzzle_images.py does `from bot.store import PuzzleImageJsonDb` at
        # module import time, so patching bot.store.PuzzleImageJsonDb would
        # miss the reference the service module already holds -- patch it
        # where it's looked up, same as test_canvas_web.py's fixture note.
        patch("bot.core.puzzle_images.PuzzleImageJsonDb", pidb),
        # Phase 4: _push_board was replaced by _notify_board, which publishes
        # straight to events.publish rather than through cog.service.notifier
        # -- the board-push assertions below patch this instead of the cog.
        patch("bot.web.actions.events.publish"),
    ]
    mocks = [p.start() for p in patches]
    pub = mocks[-1]
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx,
                             pdb=pdb, mdb=mdb, pidb=pidb, pub=pub)
    finally:
        for p in patches:
            p.stop()


async def test_listing_a_puzzles_images(web):
    resp = await web.client.get("/api/puzzles/20/images")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body == [{
        "id": 1, "media_id": 7, "media_url": "/api/media/7",
        "source_region_id": None, "position": 0,
    }]


async def test_attaching_an_upload_appends_it_and_pushes_the_board(web):
    resp = await web.client.post("/api/puzzles/20/images", json={"media_id": 7})
    assert resp.status_code == 201
    body = await resp.get_json()
    assert body["media_id"] == 7
    web.pidb.create.assert_called_once_with(
        puzzle_id=20, media_id=7, source_region_id=None, position=1)
    web.pub.assert_called_once()


async def test_attaching_media_from_another_hunt_is_refused(web):
    """The media id is client-supplied; this is the one way past media
    visibility, so it must 400, not silently cross-link another hunt's
    private upload onto this puzzle's strip."""
    web.mdb.get_by_id.return_value = _media(id=7, hunt_id=999)
    resp = await web.client.post("/api/puzzles/20/images", json={"media_id": 7})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "cross_hunt_media"
    web.pidb.create.assert_not_called()


async def test_attaching_media_with_no_media_id_is_a_400(web):
    resp = await web.client.post("/api/puzzles/20/images", json={})
    assert resp.status_code == 400
    web.pidb.create.assert_not_called()


async def test_reordering_pushes_the_board(web):
    web.pidb.get_by_puzzle.return_value = [_image(id=3), _image(id=1), _image(id=2)]
    resp = await web.client.patch("/api/puzzles/20/images",
                                  json={"ordered_ids": [3, 1, 2]})
    assert resp.status_code == 200
    web.pidb.set_positions.assert_called_once_with([3, 1, 2])
    web.pub.assert_called_once()


async def test_reordering_with_a_foreign_id_is_a_404_not_a_500(web):
    """The service raises ImageNotFound for an id from another puzzle; the
    route must turn that into a 404."""
    web.pidb.get_by_puzzle.return_value = [_image(id=1)]
    resp = await web.client.patch("/api/puzzles/20/images",
                                  json={"ordered_ids": [1, 999]})
    assert resp.status_code == 404
    web.pidb.set_positions.assert_not_called()


async def test_reordering_a_subset_of_the_images_is_a_400(web):
    """The drag UI always sends the whole strip. A short list would renumber
    only the listed rows and leave the rest sharing positions with them, so the
    route must refuse it rather than write a half-order."""
    web.pidb.get_by_puzzle.return_value = [_image(id=1), _image(id=2), _image(id=3)]
    resp = await web.client.patch("/api/puzzles/20/images",
                                  json={"ordered_ids": [3, 1]})
    assert resp.status_code == 400
    web.pidb.set_positions.assert_not_called()


async def test_detaching_an_image_pushes_the_board(web):
    resp = await web.client.delete("/api/puzzles/20/images/1")
    assert resp.status_code == 200
    web.pidb.delete.assert_called_once_with(1)
    web.pub.assert_called_once()


async def test_detaching_a_foreign_image_is_a_404(web):
    web.pidb.get_by_id.return_value = _image(id=1, puzzle_id=999)
    resp = await web.client.delete("/api/puzzles/20/images/1")
    assert resp.status_code == 404
    web.pidb.delete.assert_not_called()


async def test_puzzle_images_on_an_archived_hunt_are_refused(web):
    web.ctx["hunt"]["archived"] = True
    resp = await web.client.post("/api/puzzles/20/images", json={"media_id": 7})
    assert resp.status_code == 403
    web.pidb.create.assert_not_called()

    resp = await web.client.patch("/api/puzzles/20/images", json={"ordered_ids": []})
    assert resp.status_code == 403

    resp = await web.client.delete("/api/puzzles/20/images/1")
    assert resp.status_code == 403
    web.pidb.delete.assert_not_called()

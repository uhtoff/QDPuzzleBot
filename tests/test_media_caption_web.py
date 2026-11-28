"""A picture's caption: PATCH /api/media/<id>, GET /api/media/<id>/info, and the
caption on the drawer's image list."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.web import data as web_data
from tests.conftest import make_hunt, make_hunt_ctx, make_web_app, start_patches, stop_patches


def _media(caption=None):
    return SimpleNamespace(id=7, hunt_id=10, caption=caption)


@pytest.fixture
def web():
    app = make_web_app("actions", cog=MagicMock())
    ctx = make_hunt_ctx()
    mdb, hdb = MagicMock(), MagicMock()
    mdb.get_by_id.return_value = _media()
    mdb.puzzle_ids_using.return_value = [3, 4]
    hdb.get_by_attr.return_value = make_hunt(id=10)
    pins, audit = AsyncMock(), AsyncMock()
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions.refresh_pins_for_puzzles", pins),
        patch("bot.web.actions._audit", audit),
        patch("bot.web.actions.events.publish"),
    ]
    mocks = start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), mdb=mdb, ctx=ctx, pins=pins,
                              audit=audit, pub=mocks[-1])
    finally:
        stop_patches(patches)


async def test_setting_a_caption_saves_it_refreshes_pins_and_pushes(web):
    resp = await web.client.patch("/api/media/7", json={"caption": "  **Grid** key  "})
    assert resp.status_code == 200
    assert await resp.get_json() == {"id": 7, "caption": "**Grid** key"}
    web.mdb.update_caption.assert_called_once_with(7, "**Grid** key")
    # Every puzzle showing the picture gets its pinned Images list refreshed.
    args = web.pins.await_args.args
    assert args[0] == 10 and list(args[2]) == [3, 4]
    web.pub.assert_called_once()
    web.audit.assert_awaited_once()


async def test_an_empty_caption_clears_it(web):
    resp = await web.client.patch("/api/media/7", json={"caption": "   "})
    assert resp.status_code == 200
    assert (await resp.get_json())["caption"] is None
    web.mdb.update_caption.assert_called_once_with(7, None)


@pytest.mark.parametrize("body", [{}, {"caption": 5}, {"caption": "x" * 1001}])
async def test_a_bad_caption_is_refused_unsaved(web, body):
    resp = await web.client.patch("/api/media/7", json=body)
    assert resp.status_code == 400
    web.mdb.update_caption.assert_not_called()


async def test_captioning_an_unknown_picture_is_a_404(web):
    web.mdb.get_by_id.return_value = None
    resp = await web.client.patch("/api/media/7", json={"caption": "x"})
    assert resp.status_code == 404
    web.mdb.update_caption.assert_not_called()


async def test_captions_on_an_archived_hunt_are_read_only(web):
    web.ctx["hunt"]["archived"] = True
    resp = await web.client.patch("/api/media/7", json={"caption": "x"})
    assert resp.status_code == 403
    web.mdb.update_caption.assert_not_called()


def test_a_drawer_image_carries_its_caption():
    img = SimpleNamespace(id=1, media_id=7, source_region_id=None, position=0)
    assert web_data.puzzle_image_to_json(img, "cap")["caption"] == "cap"
    assert web_data.puzzle_image_to_json(img)["caption"] is None


async def test_media_info_returns_the_caption():
    app = make_web_app("api")
    mdb, hdb = MagicMock(), MagicMock()
    mdb.get_by_id.return_value = _media("hello")
    hdb.get_by_attr.return_value = make_hunt(id=10)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=({"id": 10}, {}))),
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().get("/api/media/7/info")
        assert resp.status_code == 200
        assert await resp.get_json() == {"id": 7, "caption": "hello"}
    finally:
        stop_patches(patches)

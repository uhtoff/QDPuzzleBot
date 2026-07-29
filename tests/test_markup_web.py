"""Markup serialisation and routes."""
import json
from types import SimpleNamespace

from bot.web import data as web_data


def _mark(**kw):
    row = dict(id=9, media_id=7, kind="pen",
               shape=json.dumps({"points": [[0.1, 0.2], [0.3, 0.4]]}),
               style=json.dumps({"colour": "#e03131", "width": 0.004,
                                 "opacity": 1, "arrow": False}),
               text=None, created_at=None)
    row.update(kw)
    return SimpleNamespace(**row)


def test_a_mark_serialises_its_shape_and_style_as_objects():
    """Both columns are parsed back rather than passed through as strings, so
    the viewer never double-decodes — same contract as region_to_json."""
    out = web_data.mark_to_json(_mark())
    assert out["shape"]["points"] == [[0.1, 0.2], [0.3, 0.4]]
    assert out["style"]["colour"] == "#e03131"
    assert out["kind"] == "pen"
    assert out["text"] is None


def test_a_text_mark_serialises_its_text():
    out = web_data.mark_to_json(_mark(kind="text", text="ROT13"))
    assert out["text"] == "ROT13"


from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_hunt_ctx, make_web_app, start_patches, stop_patches


def _media(id=7, hunt_id=10):
    return SimpleNamespace(id=id, hunt_id=hunt_id, width=1200, height=900,
                           tile_status="none", tile_levels=0)


@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
    mdb, hdb = MagicMock(), MagicMock()
    mdb.get_by_id.return_value = _media()
    hdb.get_by_attr.return_value = make_hunt(id=10)

    service = MagicMock()
    service.create_mark.return_value = _mark()
    service.update_mark.return_value = _mark()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.core.markup.create_mark", service.create_mark),
        patch("bot.core.markup.update_mark", service.update_mark),
        patch("bot.core.markup.delete_mark", service.delete_mark),
        # Phase 4: _push_board was replaced by _notify_board, which publishes
        # straight to events.publish rather than through cog.service.notifier
        # -- the board-push assertions below patch this instead of the cog.
        patch("bot.web.actions.events.publish"),
    ]
    mocks = start_patches(patches)
    pub = mocks[-1]
    try:
        yield app, service, ctx, mdb, cog, pub
    finally:
        stop_patches(patches)


async def test_creating_a_mark_returns_it_and_pushes_the_board(web):
    app, service, _ctx, _mdb, _cog, pub = web
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]},
        "style": {"colour": "#e03131", "width": 0.004},
    })
    assert resp.status_code == 201
    assert (await resp.get_json())["kind"] == "pen"
    assert service.create_mark.call_args.kwargs["media_id"] == 7
    pub.assert_called_once()


async def test_a_malformed_mark_is_a_400_not_a_500(web):
    app, service, *_ = web
    from bot.core.markup_shape import MarkupRejected
    service.create_mark.side_effect = MarkupRejected("That mark has no length.")
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.5, 0.5], [0.5, 0.5]]},
        "style": {"colour": "#e03131"},
    })
    assert resp.status_code == 400
    assert "no length" in (await resp.get_json())["error"]


async def test_markup_on_another_hunts_image_is_refused(web):
    """The media row carries the hunt, so this is the one way past media
    visibility — it must 404, not fall through to the current user's hunt."""
    app, service, _ctx, mdb, *_ = web
    mdb.get_by_id.return_value = None
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]},
        "style": {"colour": "#e03131"},
    })
    assert resp.status_code == 404
    service.create_mark.assert_not_called()


async def test_markup_on_an_archived_hunts_image_is_refused(web):
    app, service, ctx, *_ = web
    ctx["hunt"]["archived"] = True
    client = app.test_client()
    resp = await client.post("/api/media/7/markup", json={
        "mark": {"kind": "pen", "points": [[0.1, 0.2], [0.3, 0.4]]},
        "style": {"colour": "#e03131"},
    })
    assert resp.status_code == 403
    service.create_mark.assert_not_called()


async def test_erasing_a_mark_deletes_it_and_pushes_the_board(web):
    app, service, _ctx, _mdb, _cog, pub = web
    client = app.test_client()
    resp = await client.delete("/api/media/7/markup/9")
    assert resp.status_code == 200
    service.delete_mark.assert_called_once_with(mark_id=9, media_id=7)
    pub.assert_called_once()


async def test_erasing_a_missing_mark_is_a_404(web):
    app, service, *_ = web
    from bot.core.markup import MarkNotFound
    service.delete_mark.side_effect = MarkNotFound("That mark no longer exists.")
    client = app.test_client()
    resp = await client.delete("/api/media/7/markup/9")
    assert resp.status_code == 404


async def test_restyling_a_mark_returns_the_updated_row(web):
    app, service, *_ = web
    client = app.test_client()
    resp = await client.patch("/api/media/7/markup/9",
                              json={"style": {"colour": "#2f9e44"}})
    assert resp.status_code == 200
    assert service.update_mark.call_args.kwargs["mark_id"] == 9

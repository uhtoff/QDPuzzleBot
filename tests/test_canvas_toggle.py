"""Turning the Canvas view on/off per hunt (organiser level): !canvas, PATCH
/api/hunts/<uid>/canvas and the hunt detail payload. Helpers are uniquely named
and local (the parallel runner shards by file)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import (make_cog, make_ctx, make_hunt, make_hunt_ctx, make_web_app,
                            start_patches, stop_patches)


# ------------------------------------------------------------------- !canvas
async def _run_canvas(arg, *, enabled=False, hunt_found=True, organiser=True, canvases=()):
    cog = make_cog()
    hunt = make_hunt(id=10, uid="U1", name="H")
    hunt.canvas_enabled = enabled
    cog.get_hunt = MagicMock(return_value=hunt if hunt_found else None)
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
            patch("bot.cogs.puzzles._is_organiser", return_value=organiser), \
            patch("bot.cogs.puzzles.CanvasJsonDb") as cdb, \
            patch("bot.cogs.puzzles.config", SimpleNamespace(web_frontend_url="https://dash.test")):
        cdb.get_by_hunt.return_value = list(canvases)
        await Puzzles.canvas.callback(cog, ctx, arg=arg)
    return SimpleNamespace(hunt=hunt, hdb=hdb, reply=ctx.send.await_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("arg,expected", [("on", True), ("OFF", False), ("off", False), ("On", True)])
async def test_canvas_on_off_stores_through_its_setter(arg, expected):
    r = await _run_canvas(arg, enabled=not expected)
    r.hdb.set_canvas_enabled.assert_called_once_with(10, expected)
    assert r.hunt.canvas_enabled is expected
    r.hdb.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arg", ["on", "off"])
async def test_canvas_on_off_is_for_organisers_only(arg):
    r = await _run_canvas(arg, organiser=False)
    r.hdb.set_canvas_enabled.assert_not_called()
    assert "organiser" in r.reply.lower()


@pytest.mark.asyncio
async def test_canvas_rejects_other_words():
    r = await _run_canvas("maybe")
    r.hdb.set_canvas_enabled.assert_not_called()
    assert "`!canvas on`" in r.reply and "`!canvas off`" in r.reply


@pytest.mark.asyncio
async def test_canvas_outside_a_hunt_changes_nothing():
    r = await _run_canvas("on", hunt_found=False)
    r.hdb.set_canvas_enabled.assert_not_called()


@pytest.mark.asyncio
async def test_plain_canvas_says_when_the_view_is_off():
    r = await _run_canvas(None, enabled=False, canvases=[SimpleNamespace(id=1, name="Map")])
    r.hdb.set_canvas_enabled.assert_not_called()
    assert "off" in r.reply and "`!canvas on`" in r.reply
    assert "Map" not in r.reply


@pytest.mark.asyncio
async def test_plain_canvas_still_lists_canvases_when_on():
    r = await _run_canvas(None, enabled=True, canvases=[SimpleNamespace(id=1, name="Map")])
    r.hdb.set_canvas_enabled.assert_not_called()
    assert "Map" in r.reply and "https://dash.test/hunts/U1" in r.reply


# ----------------------------------------------------------------------- web
@pytest.fixture
def canvas_web():
    cog = MagicMock()
    app = make_web_app("actions", cog=cog)
    hunt = make_hunt(id=10, uid="U1", name="H")
    hdb = MagicMock()
    hdb.get_by_attr.return_value = hunt
    organiser = AsyncMock(return_value=None)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions.require_organiser", organiser),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions._audit", AsyncMock()),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), hunt=hunt, hdb=hdb, organiser=organiser)
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_patch_canvas_stores_through_its_setter(canvas_web, enabled):
    resp = await canvas_web.client.patch("/api/hunts/U1/canvas", json={"enabled": enabled})
    assert resp.status_code == 200
    assert await resp.get_json() == {"ok": True, "canvas_enabled": enabled}
    canvas_web.hdb.set_canvas_enabled.assert_called_once_with(10, enabled)
    canvas_web.organiser.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"enabled": "yes"}, {"enabled": 1}, {}, {"enabled": True, "x": 1}])
async def test_patch_canvas_rejects_anything_but_a_lone_boolean(canvas_web, body):
    resp = await canvas_web.client.patch("/api/hunts/U1/canvas", json=body)
    assert resp.status_code == 400
    canvas_web.hdb.set_canvas_enabled.assert_not_called()


@pytest.mark.asyncio
async def test_patch_canvas_refuses_a_non_organiser(canvas_web):
    from quart import jsonify
    async with canvas_web.client.app.app_context():
        canvas_web.organiser.return_value = (jsonify({"error": "insufficient_role"}), 403)
    resp = await canvas_web.client.patch("/api/hunts/U1/canvas", json={"enabled": True})
    assert resp.status_code == 403
    canvas_web.hdb.set_canvas_enabled.assert_not_called()


# ------------------------------------------------------------- hunt payload
async def _canvas_detail(hunt_row):
    app = make_web_app("api", cog=MagicMock())
    bot_app = MagicMock()
    bot_app.return_value.bot.get_guild.return_value = None
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(hunt_row, {"guild_snowflake": 123}))),
        patch("bot.web.api.data.get_hunt_puzzles", AsyncMock(return_value=[])),
        patch("bot.web.api.data.get_rollups_by_tag_group", AsyncMock(return_value={})),
        patch("bot.web.api.data.get_all_tag_groups", AsyncMock(return_value=[])),
        patch("bot.web.api.data.get_hunt_sheets", AsyncMock(return_value=[])),
        patch("bot.web.api.data.build_board", MagicMock(return_value=[])),
        patch("bot.web.api.user_can_manage", AsyncMock(return_value=False)),
        patch("bot.web.api.bot_app", bot_app),
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().get("/api/hunts/U1")
        return (await resp.get_json())["hunt"]
    finally:
        stop_patches(patches)


def _canvas_hrow(**kw):
    row = {"id": 10, "uid": "U1", "name": "H", "url": "u", "archived": False,
           "guild_id": 1, "role_id": 0, "roster_role_id": None,
           "google_sheet_id": None, "solved_sheet_id": None, "sheet_mode": "tabs"}
    row.update(kw)
    return row


@pytest.mark.asyncio
async def test_hunt_detail_carries_canvas_enabled():
    assert (await _canvas_detail(_canvas_hrow(canvas_enabled=1)))["canvas_enabled"] is True
    assert (await _canvas_detail(_canvas_hrow(canvas_enabled=0)))["canvas_enabled"] is False
    assert (await _canvas_detail(_canvas_hrow()))["canvas_enabled"] is False

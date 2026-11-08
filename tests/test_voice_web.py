"""Dashboard API for puzzle voice channels: board fields, the create / cleanup /
settings routes, and the "voice" websocket event gate."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.voice import VoiceError
from bot.web import data
from tests.conftest import (make_hunt, make_hunt_ctx, make_puzzle, make_web_app,
                            start_patches, stop_patches)
from tests.test_hunt_puzzles_payload import _Dispatcher, _row


# ------------------------------------------------------------ board payload
async def _board(rows, counts):
    fake = _Dispatcher(main=rows)
    with patch.object(data, "fetch_all", fake), \
         patch("bot.web.data.voice_presence.count", side_effect=lambda cid: counts.get(cid, 0)):
        return await data.get_hunt_puzzles({"id": 10, "google_sheet_id": "", "solved_sheet_id": ""}, "123")


@pytest.mark.asyncio
async def test_board_puzzles_carry_voice_fields_per_puzzle():
    out = await _board(
        [_row(id=1, voice_channel_id=555), _row(id=2, name="Beta", voice_channel_id=None)],
        {555: 3})
    by_id = {p["id"]: p for p in out}
    assert by_id[1]["voice_channel_id"] == "555"
    assert by_id[1]["voice_url"] == "https://discord.com/channels/123/555"
    assert by_id[1]["voice_count"] == 3
    assert by_id[2]["voice_channel_id"] is None
    assert by_id[2]["voice_url"] is None
    assert by_id[2]["voice_count"] == 0


@pytest.mark.asyncio
async def test_main_query_selects_voice_channel_id():
    fake = _Dispatcher(main=[_row()])
    with patch.object(data, "fetch_all", fake):
        await data.get_hunt_puzzles({"id": 10, "google_sheet_id": "", "solved_sheet_id": ""}, "123")
    assert any("puzzles.voice_channel_id" in q and "FROM puzzles " in q for q in fake.queries)


async def _detail(hunt_row, counts):
    app = make_web_app("api", cog=MagicMock())
    bot_app = MagicMock()
    bot_app.return_value.bot.get_guild.return_value = None
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt",
              AsyncMock(return_value=(hunt_row, {"guild_snowflake": 123}))),
        patch("bot.web.api.data.get_hunt_puzzles", AsyncMock(return_value=[])),
        patch("bot.web.api.data.get_rollups_by_tag_group", AsyncMock(return_value={})),
        patch("bot.web.api.data.get_all_tag_groups", AsyncMock(return_value=[])),
        patch("bot.web.api.data.get_hunt_sheets", AsyncMock(return_value=[])),
        patch("bot.web.api.data.build_board", MagicMock(return_value=[])),
        patch("bot.web.api.user_can_manage", AsyncMock(return_value=False)),
        patch("bot.web.api.bot_app", bot_app),
        patch("bot.web.data.voice_presence.count", side_effect=lambda cid: counts.get(cid, 0)),
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().get("/api/hunts/U1")
        return (await resp.get_json())["hunt"]
    finally:
        stop_patches(patches)


def _hrow(**kw):
    row = {"id": 10, "uid": "U1", "name": "H", "url": "u", "archived": False,
           "guild_id": 1, "role_id": 0, "roster_role_id": None,
           "google_sheet_id": None, "solved_sheet_id": None, "sheet_mode": "tabs"}
    row.update(kw)
    return row


@pytest.mark.asyncio
async def test_hunt_detail_voice_fields():
    h = await _detail(_hrow(voice_channel_id=777, voice_per_puzzle=1), {777: 4})
    assert h["voice_channel_id"] == "777"
    assert h["voice_url"] == "https://discord.com/channels/123/777"
    assert h["voice_count"] == 4
    assert h["voice_per_puzzle"] is True


@pytest.mark.asyncio
async def test_hunt_detail_voice_fields_when_unset():
    h = await _detail(_hrow(voice_channel_id=None, voice_per_puzzle=0), {})
    assert h["voice_channel_id"] is None
    assert h["voice_url"] is None
    assert h["voice_count"] == 0
    assert h["voice_per_puzzle"] is False


# ------------------------------------------------------------------- routes
@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.update_hunt_settings = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    guild = MagicMock()
    app = make_web_app("actions", cog=cog, get_guild=guild)
    hunt = make_hunt(id=10, uid="U1", name="H")
    hdb = MagicMock()
    hdb.get_by_attr.return_value = hunt
    pdb = MagicMock()
    puzzle = make_puzzle(id=7)
    pdb.get_by_attr.return_value = puzzle
    ctx = make_hunt_ctx()
    admin = AsyncMock(return_value=None)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.require_admin", admin),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.puzzles_cog", return_value=cog),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions._notify_board"),
    ]
    mocks = start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, hunt=hunt,
                              hdb=hdb, puzzle=puzzle, guild=guild, admin=admin,
                              notify=mocks[8])
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_create_voice_success(web):
    channel = MagicMock(id=555)
    with patch("bot.web.actions.voice.create_puzzle_voice",
               AsyncMock(return_value=(channel, True))) as cpv, \
         patch("bot.web.actions.voice.can_move_members", return_value=True) as cmm:
        resp = await web.client.post("/api/puzzles/7/voice")
    assert resp.status_code == 200
    assert await resp.get_json() == {
        "ok": True, "voice_channel_id": "555",
        "voice_url": "https://discord.com/channels/123/555", "created": True,
        "move_members": True}
    cmm.assert_called_once_with(web.guild, web.hunt)
    cpv.assert_awaited_once_with(web.guild, web.hunt, web.puzzle)
    web.notify.assert_called_once()


@pytest.mark.asyncio
async def test_create_voice_reports_missing_move_members(web):
    with patch("bot.web.actions.voice.create_puzzle_voice",
               AsyncMock(return_value=(MagicMock(id=555), True))), \
         patch("bot.web.actions.voice.can_move_members", return_value=False):
        resp = await web.client.post("/api/puzzles/7/voice")
    assert (await resp.get_json())["move_members"] is False


@pytest.mark.asyncio
async def test_create_voice_existing_reports_created_false(web):
    with patch("bot.web.actions.voice.create_puzzle_voice",
               AsyncMock(return_value=(MagicMock(id=555), False))):
        resp = await web.client.post("/api/puzzles/7/voice")
    assert (await resp.get_json())["created"] is False
    web.notify.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["solved", "archived", "no_hunt_category"])
async def test_create_voice_refusals_are_409(web, code):
    with patch("bot.web.actions.voice.create_puzzle_voice",
               AsyncMock(side_effect=VoiceError(code))):
        resp = await web.client.post("/api/puzzles/7/voice")
    assert resp.status_code == 409
    assert await resp.get_json() == {"error": code}


@pytest.mark.asyncio
async def test_create_voice_discord_failure_maps_to_502(web):
    import discord
    exc = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
    with patch("bot.web.actions.voice.create_puzzle_voice", AsyncMock(side_effect=exc)):
        resp = await web.client.post("/api/puzzles/7/voice")
    assert resp.status_code == 502
    assert await resp.get_json() == {"error": "discord_error"}


@pytest.mark.asyncio
async def test_cleanup_returns_removed_names(web):
    with patch("bot.web.actions.voice.cleanup_hunt",
               AsyncMock(return_value=["Alpha", "Beta"])) as ch:
        resp = await web.client.post("/api/hunts/U1/voice-cleanup")
    assert resp.status_code == 200
    assert await resp.get_json() == {"ok": True, "removed": ["Alpha", "Beta"]}
    ch.assert_awaited_once_with(web.guild, web.hunt)


@pytest.mark.asyncio
async def test_cleanup_refuses_non_admin(web):
    from quart import jsonify

    async def deny(_guild):
        return jsonify({"error": "insufficient_role"}), 403

    with patch("bot.web.actions.require_admin", deny), \
         patch("bot.web.actions.voice.cleanup_hunt", AsyncMock()) as ch:
        resp = await web.client.post("/api/hunts/U1/voice-cleanup")
    assert resp.status_code == 403
    ch.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_accepts_voice_per_puzzle(web):
    resp = await web.client.patch("/api/hunts/U1/settings", json={"voice_per_puzzle": True})
    assert resp.status_code == 200
    web.hdb.set_voice_per_puzzle.assert_called_once_with(10, True)
    # not forwarded to the generic settings updater
    for call in web.cog.service.update_hunt_settings.await_args_list:
        assert "voice_per_puzzle" not in call.kwargs


@pytest.mark.asyncio
async def test_settings_voice_per_puzzle_off_and_with_other_keys(web):
    resp = await web.client.patch(
        "/api/hunts/U1/settings", json={"voice_per_puzzle": False, "puzzle_prefix": "p"})
    assert resp.status_code == 200
    web.hdb.set_voice_per_puzzle.assert_called_once_with(10, False)
    web.cog.service.update_hunt_settings.assert_awaited_once_with(web.hunt, puzzle_prefix="p")


@pytest.mark.asyncio
async def test_settings_voice_per_puzzle_must_be_bool(web):
    resp = await web.client.patch("/api/hunts/U1/settings", json={"voice_per_puzzle": "yes"})
    assert resp.status_code == 400
    web.hdb.set_voice_per_puzzle.assert_not_called()


async def _refuse(_arg):
    from quart import jsonify
    return None, (jsonify({"error": "forbidden"}), 403)


@pytest.mark.asyncio
async def test_create_voice_refused_when_puzzle_not_visible(web):
    with patch("bot.web.actions._visible_hunt_for_puzzle", _refuse), \
         patch("bot.web.actions.voice.create_puzzle_voice", AsyncMock()) as cpv:
        resp = await web.client.post("/api/puzzles/7/voice")
    assert resp.status_code == 403
    cpv.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_refused_when_hunt_not_visible(web):
    with patch("bot.web.actions._visible_hunt_by_uid", _refuse), \
         patch("bot.web.actions.voice.cleanup_hunt", AsyncMock()) as ch:
        resp = await web.client.post("/api/hunts/U1/voice-cleanup")
    assert resp.status_code == 403
    ch.assert_not_awaited()


# --------------------------------------------------------------- websocket
def _guild_for_event(*, can_view_ids):
    def get_channel(cid):
        ch = MagicMock()
        ch.permissions_for.return_value = MagicMock(view_channel=cid in can_view_ids)
        return ch
    guild = MagicMock()
    guild.get_channel.side_effect = get_channel
    guild.get_member.return_value = MagicMock()
    bot = MagicMock()
    bot.get_guild.return_value = guild
    return MagicMock(return_value=MagicMock(bot=bot))


@pytest.mark.asyncio
async def test_ws_voice_event_follows_channel_visibility():
    """Through the real socket loop: a voice event for a channel the user cannot
    view is dropped, for one they can (puzzle text channel, or the hunt general
    channel carrying hunt_uid) it is delivered."""
    import asyncio
    from bot.web import api, events
    from tests.test_web_hardening_lows import _real_app
    ba = _guild_for_event(can_view_ids={111, 333})
    with patch("bot.web.api.session", {"user": {"id": 1}}), \
         patch("bot.web.api.config") as cfg, \
         patch("bot.web.api.bot_app", ba), \
         patch("bot.web.api.get_or_fetch_member", AsyncMock(return_value=MagicMock())), \
         patch.object(api, "resolve_membership") as resolve:
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        resolve.return_value = ({42: object()}, {})
        client = _real_app().test_client()
        async with client.websocket(
            "/api/ws/activity", headers={"Origin": "https://example.test"}
        ) as ws:
            assert (await asyncio.wait_for(ws.receive_json(), 2))["type"] == "ready"
            events.publish({"type": "voice", "guild_id": 42, "channel_id": "222", "voice_count": 9})
            events.publish({"type": "voice", "guild_id": 42, "channel_id": "111", "voice_count": 2})
            events.publish({"type": "voice", "guild_id": 42, "channel_id": "333",
                            "voice_count": 1, "hunt_uid": "U1"})
            first = await asyncio.wait_for(ws.receive_json(), 2)
            second = await asyncio.wait_for(ws.receive_json(), 2)
    assert first["channel_id"] == "111" and first["voice_count"] == 2
    assert second["channel_id"] == "333" and second["hunt_uid"] == "U1"

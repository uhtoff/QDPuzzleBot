"""POST /api/channels/<id>/messages: reply into a puzzle channel via webhook."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.conftest import make_web_app, start_patches, stop_patches

HUNT = {"id": 4, "uid": "h", "guild_id": 7, "archived": 0}
URL = "/api/channels/555/messages"


@asynccontextmanager
async def _client(*, hunt=HUNT, visible=True, view=True, send=True, timed_out=False,
                  channel_found=True, guild_found=True, member_found=True,
                  send_side_effect=None):
    member = MagicMock()
    member.is_timed_out.return_value = timed_out
    channel = MagicMock()
    channel.permissions_for.return_value = SimpleNamespace(view_channel=view, send_messages=send)
    guild = MagicMock()
    guild.get_channel.return_value = channel if channel_found else None
    app = make_web_app("api", cog=MagicMock(), get_guild=guild)
    if not guild_found:
        app.bot.get_guild.return_value = None
    send_as = AsyncMock(return_value=999, side_effect=send_side_effect)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api.data.get_hunt_by_channel_id", AsyncMock(return_value=hunt)),
        patch("bot.web.api.data.get_guilds_map",
              AsyncMock(return_value={7: {"guild_snowflake": 777}})),
        patch("bot.web.api.resolve_membership", AsyncMock(return_value=({777}, {}))),
        patch("bot.web.api.hunt_visible", lambda *a: visible),
        patch("bot.web.api.get_or_fetch_member",
              AsyncMock(return_value=member if member_found else None)),
        patch("bot.web.api.channel_relay.send_as", send_as),
    ]
    start_patches(patches)
    try:
        c = app.test_client()
        c.send_as, c.member, c.channel, c.app_ = send_as, member, channel, app
        yield c
    finally:
        stop_patches(patches)


async def _refused(c, resp, status, error):
    assert resp.status_code == status
    assert await resp.get_json() == {"error": error}
    c.send_as.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_posts_as_member():
    async with _client() as c:
        resp = await c.post(URL, json={"content": "  hello  "})
        assert resp.status_code == 201
        assert await resp.get_json() == {"id": "999"}
        c.send_as.assert_awaited_once_with(c.channel, c.member, "hello")


@pytest.mark.asyncio
async def test_reply_refuses_bang_command():
    async with _client() as c:
        await _refused(c, await c.post(URL, json={"content": "  !solve X"}), 400, "command")


@pytest.mark.asyncio
async def test_reply_refuses_empty_and_too_long():
    async with _client() as c:
        await _refused(c, await c.post(URL, json={"content": "   "}), 400, "empty")
        await _refused(c, await c.post(URL, json={"content": 5}), 400, "empty")
        await _refused(c, await c.post(URL, json={}), 400, "empty")
        await _refused(c, await c.post(URL, json={"content": "x" * 2001}), 400, "too_long")
        ok = await c.post(URL, json={"content": "x" * 2000})
        assert ok.status_code == 201


@pytest.mark.asyncio
async def test_reply_hidden_hunt_is_404():
    async with _client(visible=False) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_reply_unknown_channel_hunt_is_404():
    async with _client(hunt=None) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_reply_archived_is_403():
    async with _client(hunt={**HUNT, "archived": 1}) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 403, "archived")


@pytest.mark.asyncio
async def test_reply_without_send_permission_is_403():
    async with _client(send=False) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 403, "forbidden")


@pytest.mark.asyncio
async def test_reply_without_view_permission_is_403():
    async with _client(view=False) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 403, "forbidden")


@pytest.mark.asyncio
async def test_reply_timed_out_member_is_403():
    async with _client(timed_out=True) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 403, "forbidden")


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"channel_found": False}, {"guild_found": False},
                                {"member_found": False}])
async def test_reply_missing_channel_guild_or_member_is_404(kw):
    async with _client(**kw) as c:
        await _refused(c, await c.post(URL, json={"content": "hi"}), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_reply_discord_error_is_502():
    err = discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    async with _client(send_side_effect=err) as c:
        resp = await c.post(URL, json={"content": "hi"})
        assert resp.status_code == 502
        assert await resp.get_json() == {"error": "send_failed"}
        c.send_as.assert_awaited_once()

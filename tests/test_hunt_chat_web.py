"""GET /api/hunts/<uid>/chat: the hunt's general channel for the chat extension's
dashboard view, in the by-sheet lookup's shape."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_web_app, start_patches, stop_patches

HUNT = {"id": 4, "uid": "huntuid1", "name": "Big Hunt", "guild_id": 7, "archived": 0,
        "channel_id": 321}


@asynccontextmanager
async def _client(*, hunt=HUNT, visible=True, logged_in=True):
    app = make_web_app("api")
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}} if logged_in else {}),
        patch("bot.web.api.data.get_hunt_by_uid", AsyncMock(return_value=hunt)),
        patch("bot.web.api.data.get_guilds_map",
              AsyncMock(return_value={7: {"guild_snowflake": 777}})),
        patch("bot.web.api.resolve_membership", AsyncMock(return_value=({777}, {}))),
        patch("bot.web.api.hunt_visible", lambda *a: visible),
        patch("bot.web.api.config.web_bridge_enabled", True),
    ]
    start_patches(patches)
    try:
        yield app.test_client()
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_hunt_chat_returns_general_channel_in_lookup_shape():
    async with _client() as c:
        resp = await c.get("/api/hunts/huntuid1/chat")
        assert resp.status_code == 200
        body = await resp.get_json()
    assert body == {"bridge_enabled": True, "puzzle": {
        "id": None, "name": "Big Hunt (general)", "solved": False, "channel_id": "321",
        "discord_url": "https://discord.com/channels/777/321", "sheet_url": None,
        "hunt_uid": "huntuid1", "hunt_archived": False, "sheet_mode": None,
        "hunt_chat": True}}


@pytest.mark.asyncio
async def test_hunt_chat_hidden_hunt_is_404():
    async with _client(visible=False) as c:
        resp = await c.get("/api/hunts/huntuid1/chat")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_hunt_chat_unknown_hunt_is_404():
    async with _client(hunt=None) as c:
        resp = await c.get("/api/hunts/nope/chat")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_hunt_chat_requires_login():
    async with _client(logged_in=False) as c:
        resp = await c.get("/api/hunts/huntuid1/chat")
        assert resp.status_code == 401

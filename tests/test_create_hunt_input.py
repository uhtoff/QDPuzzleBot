"""Hunt-creation input handling: the !hunt argument parser and POST /api/hunts
body validation. Helpers are uniquely named and local to this file (the parallel
runner shards by file).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_web_app, start_patches, stop_patches


# --------------------------------------------------------------- bang command
def _hunt_cog():
    cog = make_cog()
    cog.check_is_bot_channel = AsyncMock(return_value=True)
    cog.get_guild_data = lambda ctx: MagicMock(id=1)
    cog.create_hunt = AsyncMock()
    return cog


async def _run_hunt(cog, arg):
    from bot.cogs.puzzles import Puzzles
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        await Puzzles.hunt.callback(cog, ctx, arg=arg)
    return cog.create_hunt.call_args


@pytest.mark.asyncio
async def test_hunt_parses_name_url_and_role():
    cog = _hunt_cog()
    args, kwargs = await _run_hunt(cog, "mystery:https://h/, Team A")
    assert args[1] == "mystery"
    assert args[2] == "https://h/"
    assert args[3] == "Team A"
    assert "layout_mode" not in kwargs   # the forum layout option is gone


# ------------------------------------------------------------------- web API
@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.create_hunt = AsyncMock(
        return_value=MagicMock(uid="U9", channel_id=0)  # channel_id=0 → skip welcome
    )
    cog.send_initial_hunt_channel_messages = AsyncMock()

    app = make_web_app("actions", cog=cog)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.data.get_guilds_map",
              AsyncMock(return_value={5: {"guild_snowflake": "123"}})),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
    ]
    start_patches(patches)
    try:
        yield app, cog
    finally:
        stop_patches(patches)


def _body(**extra):
    body = {"guild_id": 5, "name": "H", "url": "https://h/", "start_timestamp": 0}
    body.update(extra)
    return body


@pytest.mark.asyncio
async def test_web_create_hunt_rejects_layout_mode_as_unknown_key(web):
    # The forum layout option is gone: a client still sending layout_mode is
    # refused before any Discord/Sheets work, not silently ignored.
    app, cog = web
    client = app.test_client()
    resp = await client.post("/api/hunts", json=_body(layout_mode="forum"))
    assert resp.status_code == 400
    body = await resp.get_json()
    assert body["error"] == "bad_keys"
    assert body["unknown"] == ["layout_mode"]
    cog.service.create_hunt.assert_not_awaited()


@pytest.mark.asyncio
class TestCreateHuntRejectsBadStartTimestamp:
    """#35: a malformed start_timestamp must be rejected before any Discord/
    Sheets resources are created, not raise deep inside service.create_hunt
    after the role/category/channels/spreadsheet already exist."""

    async def test_rejects_string_timestamp(self, web):
        app, cog = web
        client = app.test_client()
        resp = await client.post("/api/hunts", json=_body(start_timestamp="1720000000"))
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "bad_start_timestamp"
        cog.service.create_hunt.assert_not_awaited()

    async def test_rejects_null_timestamp(self, web):
        app, cog = web
        client = app.test_client()
        resp = await client.post("/api/hunts", json=_body(start_timestamp=None))
        assert resp.status_code == 400
        cog.service.create_hunt.assert_not_awaited()

    async def test_rejects_out_of_range_timestamp(self, web):
        app, cog = web
        client = app.test_client()
        resp = await client.post("/api/hunts", json=_body(start_timestamp=10**20))
        assert resp.status_code == 400
        cog.service.create_hunt.assert_not_awaited()

    async def test_accepts_int_timestamp(self, web):
        app, cog = web
        client = app.test_client()
        resp = await client.post("/api/hunts", json=_body(start_timestamp=1720000000))
        assert resp.status_code == 200
        cog.service.create_hunt.assert_awaited_once()

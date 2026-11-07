"""GET /api/puzzles/by-sheet: which puzzle does a Google Sheets URL belong to."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_web_app, start_patches, stop_patches

HUNT = {"id": 4, "uid": "huntuid1", "guild_id": 7, "archived": 0, "sheet_mode": "files",
        "google_sheet_id": "LIVE", "solved_sheet_id": "SOLVED"}
PUZZLE = {"id": 9, "name": "Cube", "channel_id": 555, "solved": 0, "parent_puzzle_id": None,
          "hunt_id": 4, "google_sheet_id": "", "google_page_id": "123", "on_solved_sheet": 0}


@asynccontextmanager
async def _client(*, found=(PUZZLE, HUNT), visible=True, bridge=True, logged_in=True):
    app = make_web_app("api")
    lookup = AsyncMock(return_value=None if found is None else {"puzzle": found[0], "hunt": found[1]})
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}} if logged_in else {}),
        patch("bot.web.api.data.find_puzzle_by_sheet", lookup),
        patch("bot.web.api.data.get_guilds_map",
              AsyncMock(return_value={7: {"guild_snowflake": 777}})),
        patch("bot.web.api.resolve_membership", AsyncMock(return_value=({777}, {}))),
        patch("bot.web.api.hunt_visible", lambda *a: visible),
        patch("bot.web.api.config.web_bridge_enabled", bridge),
    ]
    start_patches(patches)
    try:
        client = app.test_client()
        client.lookup = lookup
        yield client
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_lookup_returns_visible_puzzle_and_bridge_flag():
    async with _client() as c:
        resp = await c.get("/api/puzzles/by-sheet?spreadsheet=LIVE&gid=123")
        body = await resp.get_json()
        assert resp.status_code == 200
        assert body["bridge_enabled"] is True
        assert body["puzzle"] == {
            "id": 9, "name": "Cube", "solved": False, "channel_id": "555",
            "discord_url": "https://discord.com/channels/777/555",
            "sheet_url": body["puzzle"]["sheet_url"], "hunt_uid": "huntuid1",
            "hunt_archived": False, "sheet_mode": "files"}
        assert "LIVE" in body["puzzle"]["sheet_url"] and "gid=123" in body["puzzle"]["sheet_url"]
        c.lookup.assert_awaited_once_with("LIVE", 123)


@pytest.mark.asyncio
async def test_lookup_bridge_disabled_and_non_integer_gid_is_absent():
    async with _client(bridge=False) as c:
        resp = await c.get("/api/puzzles/by-sheet?spreadsheet=LIVE&gid=abc")
        assert (await resp.get_json())["bridge_enabled"] is False
        c.lookup.assert_awaited_once_with("LIVE", None)


@pytest.mark.asyncio
async def test_lookup_hidden_hunt_is_404():
    async with _client(visible=False) as c:
        resp = await c.get("/api/puzzles/by-sheet?spreadsheet=LIVE&gid=123")
        assert resp.status_code == 404
        assert await resp.get_json() == {"error": "not_found_or_forbidden"}


@pytest.mark.asyncio
async def test_lookup_unknown_sheet_is_404():
    async with _client(found=None) as c:
        resp = await c.get("/api/puzzles/by-sheet?spreadsheet=NOPE&gid=1")
        assert resp.status_code == 404
        assert await resp.get_json() == {"error": "not_found_or_forbidden"}


@pytest.mark.asyncio
async def test_lookup_missing_spreadsheet_is_404_without_lookup():
    async with _client() as c:
        resp = await c.get("/api/puzzles/by-sheet?gid=1")
        assert resp.status_code == 404
        c.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_requires_login():
    async with _client(logged_in=False) as c:
        resp = await c.get("/api/puzzles/by-sheet?spreadsheet=LIVE&gid=123")
        assert resp.status_code == 401

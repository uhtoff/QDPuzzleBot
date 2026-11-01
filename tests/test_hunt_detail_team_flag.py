"""GET /api/hunts/<uid> says whether the hunt is a team hunt, without leaking
the hub role id."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_web_app, start_patches, stop_patches


async def _detail(hunt_row):
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
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().get("/api/hunts/U1")
        return resp.status_code, await resp.get_json()
    finally:
        stop_patches(patches)


def _row(roster_role_id):
    return {"id": 10, "uid": "U1", "name": "H", "url": "u", "archived": False,
            "guild_id": 1, "role_id": 0, "roster_role_id": roster_role_id,
            "google_sheet_id": None, "solved_sheet_id": None,
            "sheet_mode": "tabs"}


@pytest.mark.asyncio
async def test_team_hunt_flagged_without_exposing_role_id():
    status, body = await _detail(_row(987654321))
    assert status == 200
    assert body["hunt"]["is_team_hunt"] is True
    assert "987654321" not in str(body)


@pytest.mark.asyncio
async def test_plain_hunt_not_flagged():
    for empty in (None, 0):
        status, body = await _detail(_row(empty))
        assert status == 200
        assert body["hunt"]["is_team_hunt"] is False


@pytest.mark.asyncio
async def test_hunt_detail_sends_sheet_urls_and_mode():
    row = _row(0)
    row.update(google_sheet_id="LIVE", solved_sheet_id="SOLVED", sheet_mode="files")
    _, body = await _detail(row)
    assert body["hunt"]["sheet_url"] == "https://docs.google.com/spreadsheets/d/LIVE"
    assert body["hunt"]["solved_sheet_url"] == "https://docs.google.com/spreadsheets/d/SOLVED"
    assert body["hunt"]["sheet_mode"] == "files"


@pytest.mark.asyncio
async def test_hunt_detail_without_a_solved_sheet_sends_none():
    row = _row(0)
    row.update(google_sheet_id="LIVE", solved_sheet_id="")
    _, body = await _detail(row)
    assert body["hunt"]["solved_sheet_url"] is None


@pytest.mark.asyncio
async def test_hunt_detail_sends_the_drive_folder_link_for_a_files_hunt():
    row = _row(0)
    row.update(sheet_mode="files", drive_folder_id="FOLD")
    _, body = await _detail(row)
    assert body["hunt"]["drive_folder_url"] == "https://drive.google.com/drive/folders/FOLD"


@pytest.mark.asyncio
async def test_hunt_detail_without_a_drive_folder_sends_none():
    for row in (_row(0), {**_row(0), "drive_folder_id": ""}):
        _, body = await _detail(row)
        assert body["hunt"]["drive_folder_url"] is None

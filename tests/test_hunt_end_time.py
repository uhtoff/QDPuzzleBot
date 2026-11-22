"""Hunt end time (migration 062): optional end_timestamp, None = no end date.

Covers PuzzleService.create_hunt / update_hunt_settings, POST /api/hunts,
PATCH /api/hunts/<uid>/settings, the hunt detail payload and !set_end.
Helpers are uniquely named and local (the parallel runner shards by file)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import (make_cog, make_ctx, make_hunt, make_hunt_ctx, make_web_app,
                            start_patches, stop_patches)

START = 1_700_000_000
END = START + 48 * 3600


@pytest.fixture(autouse=True)
def _no_organiser_reminder_end():
    with patch("bot.core.role_gate.organiser_reminder", return_value=None):
        yield


def test_hunt_data_defaults_to_no_end_date():
    assert HuntData().end_timestamp is None


# ------------------------------------------------------------------ service
async def test_update_settings_sets_and_clears_end_timestamp():
    bot = MagicMock()
    bot.get_cog.return_value = None
    svc = PuzzleService(bot)
    hunt = make_hunt(start_timestamp=START)
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        await svc.update_hunt_settings(hunt, end_timestamp=END)
        assert hunt.end_timestamp == END
        await svc.update_hunt_settings(hunt, end_timestamp=None)
        assert hunt.end_timestamp is None
        await svc.update_hunt_settings(hunt, url="https://x/")  # unprovided: untouched
    assert hunt.end_timestamp is None
    assert hdb.commit.call_count == 3


# ---------------------------------------------------------------------- web
@pytest.fixture
def end_web():
    cog = MagicMock()
    cog.service.create_hunt = AsyncMock(return_value=MagicMock(uid="U9", channel_id=0))
    cog.service.update_hunt_settings = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)
    hunt = make_hunt(id=10, uid="U1", name="H", start_timestamp=START)
    hdb = MagicMock()
    hdb.get_by_attr.return_value = hunt
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.data.get_guilds_map",
              AsyncMock(return_value={5: {"guild_snowflake": "123"}})),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._visible_hunt_by_uid",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions.puzzles_cog", return_value=cog),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions._audit", AsyncMock()),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), cog=cog, hunt=hunt)
    finally:
        stop_patches(patches)


def _end_body(**extra):
    body = {"guild_id": 5, "name": "H", "url": "https://h/", "start_timestamp": START}
    body.update(extra)
    return body


async def test_create_without_end_means_no_end_date(end_web):
    resp = await end_web.client.post("/api/hunts", json=_end_body())
    assert resp.status_code == 200
    assert "end_timestamp" not in end_web.cog.service.create_hunt.call_args.kwargs


async def test_create_forwards_end_timestamp(end_web):
    resp = await end_web.client.post("/api/hunts", json=_end_body(end_timestamp=END))
    assert resp.status_code == 200
    assert end_web.cog.service.create_hunt.call_args.kwargs["end_timestamp"] == END


@pytest.mark.parametrize("end,error", [
    (START, "end_before_start"), (START - 1, "end_before_start"),
    ("123", "bad_end_timestamp"), (True, "bad_end_timestamp"), (10**20, "bad_end_timestamp"),
])
async def test_create_rejects_bad_end_before_any_work(end_web, end, error):
    resp = await end_web.client.post("/api/hunts", json=_end_body(end_timestamp=end))
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == error
    end_web.cog.service.create_hunt.assert_not_awaited()


async def test_settings_sets_and_clears_end(end_web):
    resp = await end_web.client.patch("/api/hunts/U1/settings", json={"end_timestamp": END})
    assert resp.status_code == 200
    assert end_web.cog.service.update_hunt_settings.call_args.kwargs == {"end_timestamp": END}
    resp = await end_web.client.patch("/api/hunts/U1/settings", json={"end_timestamp": None})
    assert resp.status_code == 200
    assert end_web.cog.service.update_hunt_settings.call_args.kwargs == {"end_timestamp": None}


async def test_settings_rejects_end_not_after_current_start(end_web):
    resp = await end_web.client.patch("/api/hunts/U1/settings", json={"end_timestamp": START})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "end_before_start"
    end_web.cog.service.update_hunt_settings.assert_not_awaited()


async def test_settings_checks_end_against_the_new_start(end_web):
    # Moving the start later in the same patch: the end must clear the NEW start.
    resp = await end_web.client.patch(
        "/api/hunts/U1/settings", json={"start_timestamp": END, "end_timestamp": END - 1})
    assert resp.status_code == 400
    end_web.cog.service.update_hunt_settings.assert_not_awaited()


async def test_settings_refuses_moving_start_past_the_existing_end(end_web):
    end_web.hunt.end_timestamp = END
    resp = await end_web.client.patch("/api/hunts/U1/settings", json={"start_timestamp": END})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "end_before_start"
    end_web.cog.service.update_hunt_settings.assert_not_awaited()


async def test_hunt_detail_carries_end_timestamp():
    app = make_web_app("api", cog=MagicMock())
    bot_app = MagicMock()
    bot_app.return_value.bot.get_guild.return_value = None
    row = {"id": 10, "uid": "U1", "name": "H", "url": "u", "archived": False,
           "guild_id": 1, "role_id": 0, "roster_role_id": None, "google_sheet_id": None,
           "solved_sheet_id": None, "sheet_mode": "tabs", "end_timestamp": END}
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt",
              AsyncMock(return_value=(row, {"guild_snowflake": 123}))),
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
        assert (await resp.get_json())["hunt"]["end_timestamp"] == END
    finally:
        stop_patches(patches)


# --------------------------------------------------------------- !set_end
def _set_end_env():
    cog = make_cog()
    hunt = make_hunt(start_timestamp=START)
    cog.get_channel_type = lambda ctx: "Hunt"
    cog.get_hunt = lambda ctx: hunt
    cog.service = MagicMock()
    cog.service.update_hunt_settings = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    ctx = make_ctx()
    ctx.send = AsyncMock()
    return cog, ctx


@pytest.mark.parametrize("arg,expected", [(str(END), END), ("none", None), ("None", None)])
async def test_set_end_sets_or_clears(arg, expected):
    cog, ctx = _set_end_env()
    await Puzzles.set_end.callback(cog, ctx, arg)
    cog.service.update_hunt_settings.assert_awaited_once()
    assert cog.service.update_hunt_settings.call_args.kwargs == {"end_timestamp": expected}
    assert ctx.send.await_args.args[0].startswith(":white_check_mark:")


@pytest.mark.parametrize("arg", ["tomorrow", str(START)])
async def test_set_end_refuses_bad_or_early_end(arg):
    cog, ctx = _set_end_env()
    await Puzzles.set_end.callback(cog, ctx, arg)
    cog.service.update_hunt_settings.assert_not_awaited()
    assert ctx.send.await_args.args[0].startswith(":x:")

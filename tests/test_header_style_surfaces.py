"""Choosing the header style ("full" / "compact") on every surface: !hunt,
!update_settings, PuzzleService.create_hunt, POST /api/hunts, PATCH
/api/hunts/<uid>/settings and the hunt detail payload.
Helpers are uniquely named and local (the parallel runner shards by file)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.store import HuntData
from tests.conftest import (make_cog, make_ctx, make_hunt, make_hunt_ctx,
                            make_web_app, start_patches, stop_patches)


@pytest.fixture(autouse=True)
def _no_organiser_reminder():
    with patch("bot.core.role_gate.organiser_reminder", return_value=None):
        yield


# --------------------------------------------------------------- !hunt parser
def _hunt_cog():
    cog = make_cog()
    cog.check_is_bot_channel = AsyncMock(return_value=True)
    cog.get_guild_data = lambda ctx: MagicMock(id=1)
    cog.create_hunt = AsyncMock()
    return cog


async def _run_hunt(arg):
    cog = _hunt_cog()
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        await Puzzles.hunt.callback(cog, make_ctx(), arg=arg)
    return cog.create_hunt.call_args


@pytest.mark.asyncio
async def test_hunt_default_is_compact():
    _, kwargs = await _run_hunt("m:https://h/")
    assert kwargs["header_style"] == "compact"


@pytest.mark.asyncio
@pytest.mark.parametrize("arg,sheet_mode,voice,role", [
    ("m:https://h/, full", "tabs", None, None),
    ("m:https://h/, FULL", "tabs", None, None),
    ("m:https://h/, full, files", "files", None, None),
    ("m:https://h/, files, full, voice", "files", True, None),
    ("m:https://h/, Team A, full", "tabs", None, "Team A"),
])
async def test_hunt_full_token(arg, sheet_mode, voice, role):
    args, kwargs = await _run_hunt(arg)
    assert kwargs["header_style"] == "full"
    assert kwargs["sheet_mode"] == sheet_mode
    assert kwargs["voice_per_puzzle"] == voice
    assert (args[1], args[2], args[3]) == ("m", "https://h/", role)


@pytest.mark.asyncio
async def test_create_hunt_wrapper_forwards_header_style_to_service():
    cog = make_cog()
    cog.check_available_space = AsyncMock(return_value=True)
    cog.get_guild_data = lambda ctx: MagicMock(id=1)
    cog.bot = MagicMock()
    cog.service.create_hunt = AsyncMock(return_value=MagicMock(role_id=0, channel_id=0))
    with patch("bot.core.notifier.DiscordNotifier") as n:
        n.return_value.welcome = AsyncMock()
        await Puzzles.create_hunt(cog, make_ctx(), "m", "u", None, header_style="full")
    assert cog.service.create_hunt.call_args.kwargs["header_style"] == "full"


# ------------------------------------------------------------------- service
def _svc_guild():
    guild = MagicMock()
    guild.id = 999
    guild.categories = []
    guild.roles = []
    guild.create_category = AsyncMock(return_value=MagicMock(id=111))
    guild.create_text_channel = AsyncMock(return_value=MagicMock(id=222))
    guild.create_role = AsyncMock(return_value=MagicMock(id=333))
    return guild


def _svc():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


@pytest.mark.asyncio
async def test_service_create_hunt_rejects_bad_header_style_before_discord():
    svc, guild = _svc(), _svc_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        with pytest.raises(RuntimeError, match="bad_header_style"):
            await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                                  header_style="Compact")
    guild.create_category.assert_not_awaited()
    hdb.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,expected", [({"header_style": "full"}, "full"),
                                             ({}, "compact")])
async def test_service_create_hunt_stores_the_style(kwargs, expected):
    svc, guild = _svc(), _svc_guild()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(guild, name="H", url="https://h/",
                                     start_timestamp=0, **kwargs)
    hdb.set_header_style.assert_called_once_with(hunt.id, expected)
    assert hunt.header_style == expected


# ----------------------------------------------------------- !update_settings
@pytest.fixture
def env():
    cog = make_cog()
    hunt = HuntData(name="H", id=10)
    cog.get_settings = lambda ctx: hunt
    cog.save_settings = MagicMock()
    cog.service.notifier.board_changed = AsyncMock()
    ctx = make_ctx()
    ctx.send = AsyncMock()
    ctx.invoked_with = "update_settings"
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        yield MagicMock(cog=cog, ctx=ctx, hunt=hunt, hdb=hdb)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [("compact", "compact"), ("FULL", "full")])
async def test_update_settings_stores_header_style_through_its_setter(env, raw, expected):
    await Puzzles.update_settings.callback(env.cog, env.ctx, "header_style", raw)
    env.hdb.set_header_style.assert_called_once_with(10, expected)
    assert env.hunt.header_style == expected
    env.cog.save_settings.assert_not_called()
    assert env.ctx.send.await_args.args[0].startswith(
        ":white_check_mark: Updated `header_style=")


@pytest.mark.asyncio
async def test_update_settings_rejects_an_unknown_header_style(env):
    before = env.hunt.header_style
    await Puzzles.update_settings.callback(env.cog, env.ctx, "header_style", "banana")
    env.hdb.set_header_style.assert_not_called()
    assert env.hunt.header_style == before
    assert env.ctx.send.await_args.args[0].startswith(":x:")


@pytest.mark.asyncio
async def test_update_puzzle_settings_refuses_header_layout():
    from bot.store import PuzzleData
    cog = make_cog()
    puzzle = PuzzleData(name="P", id=3)
    cog.get_puzzle = lambda ctx: puzzle
    ctx = make_ctx()
    ctx.send = AsyncMock()
    ctx.invoked_with = "update_puzzle_settings"
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb:
        await Puzzles.update_settings.callback(cog, ctx, "header_layout", "compact")
    pdb.commit.assert_not_called()
    assert "`header_style`" in ctx.send.await_args.args[0]


# ----------------------------------------------------------------------- web
@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.create_hunt = AsyncMock(return_value=MagicMock(uid="U9", channel_id=0))
    cog.service.update_hunt_settings = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    cog.send_initial_hunt_channel_messages = AsyncMock()
    app = make_web_app("actions", cog=cog)
    hunt = make_hunt(id=10, uid="U1", name="H")
    hdb = MagicMock()
    hdb.get_by_attr.return_value = hunt
    ctx = make_hunt_ctx()
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.data.get_guilds_map",
              AsyncMock(return_value={5: {"guild_snowflake": "123"}})),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.puzzles_cog", return_value=cog),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions._audit", AsyncMock()),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, hunt=hunt, hdb=hdb)
    finally:
        stop_patches(patches)


def _body(**extra):
    body = {"guild_id": 5, "name": "H", "url": "https://h/", "start_timestamp": 0}
    body.update(extra)
    return body


@pytest.mark.asyncio
async def test_web_create_hunt_forwards_header_style(web):
    resp = await web.client.post("/api/hunts", json=_body(header_style="full"))
    assert resp.status_code == 200
    assert web.cog.service.create_hunt.call_args.kwargs["header_style"] == "full"


@pytest.mark.asyncio
async def test_web_create_hunt_rejects_bad_header_style(web):
    resp = await web.client.post("/api/hunts", json=_body(header_style="Full"))
    assert resp.status_code == 400
    assert await resp.get_json() == {"error": "bad_header_style"}
    web.cog.service.create_hunt.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_accepts_header_style(web):
    resp = await web.client.patch("/api/hunts/U1/settings", json={"header_style": "full"})
    assert resp.status_code == 200
    web.hdb.set_header_style.assert_called_once_with(10, "full")
    assert web.hunt.header_style == "full"
    assert "header_style" not in web.cog.service.update_hunt_settings.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["Compact", True, None])
async def test_settings_header_style_must_be_a_known_value(web, bad):
    resp = await web.client.patch("/api/hunts/U1/settings", json={"header_style": bad})
    assert resp.status_code == 400
    assert await resp.get_json() == {"error": "bad_value", "field": "header_style"}
    web.hdb.set_header_style.assert_not_called()


# ------------------------------------------------------------- hunt payload
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
async def test_hunt_detail_carries_header_style():
    assert (await _detail(_hrow(header_style="full"), {}))["header_style"] == "full"
    assert (await _detail(_hrow(header_style="compact"), {}))["header_style"] == "compact"
    assert (await _detail(_hrow(), {}))["header_style"] == "full"

"""Choosing the sheet mode ("tabs" / "files") on every surface: !hunt, !sheet_mode,
!use_solved_sheet, POST /api/hunts, PATCH /api/hunts/<uid>/sheet-mode and
POST /api/hunts/<uid>/solved-sheet.  Setup-flow threading lives in the setup tests.
Helpers are uniquely named and local (the parallel runner shards by file)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_cog, make_ctx, make_hunt, make_hunt_ctx,
                            make_web_app, start_patches, stop_patches)


@pytest.fixture(autouse=True)
def _no_organiser_reminder():
    """Mock guilds would send organiser_reminder to the real settings DB; tests
    that care about the reminder patch it themselves (nested patch wins)."""
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
    from bot.cogs.puzzles import Puzzles
    cog = _hunt_cog()
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        await Puzzles.hunt.callback(cog, make_ctx(), arg=arg)
    return cog.create_hunt.call_args


@pytest.mark.asyncio
async def test_hunt_default_is_tabs():
    _, kwargs = await _run_hunt("m:https://h/")
    assert kwargs["sheet_mode"] == "tabs"


@pytest.mark.asyncio
async def test_hunt_files_token_selects_files():
    args, kwargs = await _run_hunt("m:https://h/, files")
    assert kwargs["sheet_mode"] == "files"
    assert "layout_mode" not in kwargs
    assert (args[1], args[2], args[3]) == ("m", "https://h/", None)


@pytest.mark.asyncio
async def test_hunt_files_token_is_case_insensitive():
    _, kwargs = await _run_hunt("m:https://h/, FILES")
    assert kwargs["sheet_mode"] == "files"


@pytest.mark.asyncio
async def test_create_hunt_wrapper_forwards_sheet_mode_to_service():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.check_available_space = AsyncMock(return_value=True)
    cog.get_guild_data = lambda ctx: MagicMock(id=1)
    cog.bot = MagicMock()
    cog.service.create_hunt = AsyncMock(return_value=MagicMock(role_id=0, channel_id=0))
    with patch("bot.core.notifier.DiscordNotifier") as n:
        n.return_value.welcome = AsyncMock()
        await Puzzles.create_hunt(cog, make_ctx(), "m", "u", None, sheet_mode="files")
    assert cog.service.create_hunt.call_args.kwargs["sheet_mode"] == "files"


# ------------------------------------------------------------- !sheet_mode
def _mode_cog(hunt):
    cog = make_cog(hunt=hunt)
    cog.get_channel_type = lambda ctx: "Hunt"
    cog.service.set_sheet_mode = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    return cog


async def _run_sheet_mode(cog, ctx, arg=None):
    from bot.cogs.puzzles import Puzzles
    with patch("bot.cogs.puzzles.GuildSettingsDb") as gdb:
        gdb.get.return_value = SimpleNamespace(drive_parent_id="PARENT")
        await Puzzles.sheet_mode.callback(cog, ctx, arg)


def _said(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


@pytest.mark.asyncio
async def test_sheet_mode_without_argument_reports_current():
    hunt = make_hunt(sheet_mode="files")
    cog, ctx = _mode_cog(hunt), make_ctx()
    await _run_sheet_mode(cog, ctx)
    assert "files" in _said(ctx)
    cog.service.set_sheet_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_sheet_mode_switches_with_the_guild_drive_parent():
    hunt = make_hunt(sheet_mode="tabs")
    cog, ctx = _mode_cog(hunt), make_ctx()
    await _run_sheet_mode(cog, ctx, "files")
    cog.service.set_sheet_mode.assert_awaited_once_with(hunt, "files", guild_parent_id="PARENT")
    assert ":white_check_mark:" in _said(ctx)
    # The pinned welcome is re-sent so it gains (or loses) the Drive folder link.
    cog.service.notifier.refresh_info.assert_awaited_once_with(ctx.guild, hunt)


@pytest.mark.asyncio
@pytest.mark.parametrize("slug,needle", [
    ("sheet_mode_locked", "no puzzles"),
    ("files_mode_no_solved_sheet", "solved"),
    ("gsheet_unavailable", "Google"),
    ("bad_sheet_mode", "tabs"),
])
async def test_sheet_mode_refusals_print_the_reason(slug, needle):
    hunt = make_hunt(sheet_mode="tabs")
    cog, ctx = _mode_cog(hunt), make_ctx()
    cog.service.set_sheet_mode.side_effect = RuntimeError(slug)
    await _run_sheet_mode(cog, ctx, "files")
    said = _said(ctx)
    assert said.startswith(":x:") and needle in said


@pytest.mark.asyncio
async def test_sheet_mode_rejects_an_unknown_value_before_the_service():
    hunt = make_hunt(sheet_mode="tabs")
    cog, ctx = _mode_cog(hunt), make_ctx()
    await _run_sheet_mode(cog, ctx, "bogus")
    assert ":x:" in _said(ctx)
    cog.service.set_sheet_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_sheet_mode_outside_a_hunt_channel_refuses():
    hunt = make_hunt(sheet_mode="tabs")
    cog, ctx = _mode_cog(hunt), make_ctx()
    cog.get_channel_type = lambda ctx: "Puzzle"
    await _run_sheet_mode(cog, ctx, "files")
    assert ":x:" in _said(ctx)
    cog.service.set_sheet_mode.assert_not_awaited()


# -------------------------------------------------------- !use_solved_sheet
@pytest.mark.asyncio
async def test_use_solved_sheet_on_a_files_hunt_says_why():
    from bot.cogs.puzzles import Puzzles
    hunt = make_hunt(sheet_mode="files")
    cog, ctx = make_cog(hunt=hunt), make_ctx()
    cog.get_channel_type = lambda ctx: "Hunt"
    cog.service.create_hunt_solved_sheet = AsyncMock(
        side_effect=RuntimeError("files_mode_no_solved_sheet"))
    cog.info = AsyncMock()
    await Puzzles.use_solved_sheet.callback(cog, ctx)
    said = _said(ctx)
    assert "one spreadsheet per puzzle" in said
    cog.info.assert_not_awaited()


# ------------------------------------------------------------------- web
@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.create_hunt = AsyncMock(return_value=MagicMock(uid="U9", channel_id=0))
    cog.service.set_sheet_mode = AsyncMock()
    cog.service.notifier.refresh_info = AsyncMock()
    cog.service.create_hunt_solved_sheet = AsyncMock(return_value="S")
    cog.send_initial_hunt_channel_messages = AsyncMock()
    app = make_web_app("actions", cog=cog)
    hunt = make_hunt(id=10, sheet_mode="tabs", uid="U1", name="H")
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
        patch("bot.web.actions.GuildSettingsDb"),
        patch("bot.web.actions._audit", AsyncMock()),
    ]
    mocks = start_patches(patches)
    mocks[6].get.return_value = SimpleNamespace(drive_parent_id="PARENT")
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, hunt=hunt,
                              audit=mocks[6+1], gdb=mocks[6])
    finally:
        stop_patches(patches)


def _body(**extra):
    body = {"guild_id": 5, "name": "H", "url": "https://h/", "start_timestamp": 0}
    body.update(extra)
    return body


@pytest.mark.asyncio
async def test_web_create_hunt_forwards_sheet_mode(web):
    resp = await web.client.post("/api/hunts", json=_body(sheet_mode="files"))
    assert resp.status_code == 200
    assert web.cog.service.create_hunt.call_args.kwargs["sheet_mode"] == "files"


@pytest.mark.asyncio
async def test_web_create_hunt_rejects_bad_sheet_mode(web):
    resp = await web.client.post("/api/hunts", json=_body(sheet_mode="bogus"))
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "bad_sheet_mode"
    web.cog.service.create_hunt.assert_not_awaited()


@pytest.mark.asyncio
async def test_web_create_hunt_files_with_solved_sheet_is_409(web):
    web.cog.service.create_hunt.side_effect = RuntimeError("files_mode_no_solved_sheet")
    resp = await web.client.post(
        "/api/hunts", json=_body(sheet_mode="files", create_solved_sheet=True))
    assert resp.status_code == 409
    assert (await resp.get_json())["error"] == "files_mode_no_solved_sheet"


@pytest.mark.asyncio
async def test_patch_sheet_mode_switches_under_the_lock(web):
    resp = await web.client.patch("/api/hunts/U1/sheet-mode", json={"sheet_mode": "files"})
    assert resp.status_code == 200
    assert (await resp.get_json()) == {"ok": True, "sheet_mode": "files"}
    web.cog.service.set_sheet_mode.assert_awaited_once_with(
        web.hunt, "files", guild_parent_id="PARENT")
    web.audit.assert_awaited()


@pytest.mark.asyncio
async def test_patch_sheet_mode_re_pins_the_welcome(web):
    guild = MagicMock()
    web.cog.service.notifier.refresh_info = AsyncMock()
    with patch("bot.web.actions.bot_app") as app:
        app.return_value.bot.get_guild.return_value = guild
        resp = await web.client.patch("/api/hunts/U1/sheet-mode", json={"sheet_mode": "files"})
    assert resp.status_code == 200
    web.cog.service.notifier.refresh_info.assert_awaited_once_with(guild, web.hunt)


@pytest.mark.asyncio
async def test_patch_sheet_mode_bad_value_is_400(web):
    for body in ({"sheet_mode": "bogus"}, {}, {"sheet_mode": 3}):
        resp = await web.client.patch("/api/hunts/U1/sheet-mode", json=body)
        assert resp.status_code == 400, body
        assert (await resp.get_json())["error"] == "bad_sheet_mode"
    web.cog.service.set_sheet_mode.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("slug,status", [
    ("sheet_mode_locked", 409),
    ("files_mode_no_solved_sheet", 409),
    ("bad_sheet_mode", 400),
    ("gsheet_unavailable", 503),
])
async def test_patch_sheet_mode_maps_refusals(web, slug, status):
    web.cog.service.set_sheet_mode.side_effect = RuntimeError(slug)
    resp = await web.client.patch("/api/hunts/U1/sheet-mode", json={"sheet_mode": "files"})
    assert resp.status_code == status
    assert (await resp.get_json())["error"] == slug


@pytest.mark.asyncio
async def test_solved_sheet_route_on_a_files_hunt_is_409(web):
    web.cog.service.create_hunt_solved_sheet.side_effect = RuntimeError(
        "files_mode_no_solved_sheet")
    resp = await web.client.post("/api/hunts/U1/solved-sheet")
    assert resp.status_code == 409
    assert (await resp.get_json())["error"] == "files_mode_no_solved_sheet"


# ------------------------------------------------- organiser reminder at creation
@pytest.mark.asyncio
async def test_web_create_hunt_returns_the_organiser_reminder(web):
    with patch("bot.web.actions.role_gate.organiser_reminder",
               return_value="On this server only Organiser ...") as rem:
        resp = await web.client.post("/api/hunts", json=_body())
    assert (await resp.get_json())["organiser_reminder"] == "On this server only Organiser ..."
    assert rem.call_args.kwargs["markdown"] is False


@pytest.mark.asyncio
async def test_web_create_hunt_omits_reminder_on_an_ungated_server(web):
    with patch("bot.web.actions.role_gate.organiser_reminder", return_value=None):
        resp = await web.client.post("/api/hunts", json=_body())
    assert "organiser_reminder" not in await resp.get_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("reminder", [":warning: gated", None])
async def test_hunt_command_posts_the_organiser_reminder(reminder):
    from bot.cogs.puzzles import Puzzles
    cog = _hunt_cog()
    cog.create_hunt = AsyncMock(return_value=MagicMock())
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.HuntJsonDb") as hdb, \
         patch("bot.cogs.puzzles.role_gate.organiser_reminder", return_value=reminder):
        hdb.check_duplicates.return_value = False
        await Puzzles.hunt.callback(cog, ctx, arg="m:https://u")
    sent = [c.args[0] for c in ctx.send.call_args_list]
    assert sent[0].startswith(":white_check_mark:")
    assert (":warning: gated" in sent) is (reminder is not None)
    assert len(sent) == (2 if reminder else 1)


# ------------------------------------------------- voice-for-every-puzzle at creation
@pytest.mark.asyncio
@pytest.mark.parametrize("arg,expected_mode,expected_role", [
    ("m:https://h/, voice", "tabs", None),
    ("m:https://h/, files, voice", "files", None),
    ("m:https://h/, voice, files", "files", None),
    ("m:https://h/, Team A, Voice", "tabs", "Team A"),
])
async def test_hunt_voice_token(arg, expected_mode, expected_role):
    args, kwargs = await _run_hunt(arg)
    assert kwargs["voice_per_puzzle"] is True
    assert kwargs["sheet_mode"] == expected_mode
    assert (args[1], args[2], args[3]) == ("m", "https://h/", expected_role)


@pytest.mark.asyncio
async def test_hunt_without_voice_token_leaves_the_server_default():
    _, kwargs = await _run_hunt("m:https://h/, files")
    assert kwargs["voice_per_puzzle"] is None


@pytest.mark.asyncio
async def test_create_hunt_wrapper_forwards_voice_choice_to_service():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.check_available_space = AsyncMock(return_value=True)
    cog.get_guild_data = lambda ctx: MagicMock(id=1)
    cog.bot = MagicMock()
    cog.service.create_hunt = AsyncMock(return_value=MagicMock(role_id=0, channel_id=0))
    with patch("bot.core.notifier.DiscordNotifier") as dn:
        dn.return_value.welcome = AsyncMock()
        await Puzzles.create_hunt(cog, make_ctx(), "m", "u", None, voice_per_puzzle=True)
    assert cog.service.create_hunt.call_args.kwargs["voice_per_puzzle"] is True


@pytest.mark.asyncio
async def test_web_create_hunt_forwards_voice_per_puzzle(web):
    resp = await web.client.post("/api/hunts", json=_body(voice_per_puzzle=True))
    assert resp.status_code == 200
    assert web.cog.service.create_hunt.call_args.kwargs["voice_per_puzzle"] is True


@pytest.mark.asyncio
async def test_web_create_hunt_rejects_non_bool_voice_per_puzzle(web):
    resp = await web.client.post("/api/hunts", json=_body(voice_per_puzzle="yes"))
    assert resp.status_code == 400
    web.cog.service.create_hunt.assert_not_awaited()

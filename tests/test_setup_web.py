"""Dashboard setup API: GET/POST /api/guilds/<id>/setup."""
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core.server_setup import SetupOutcome
from tests.conftest import make_web_app, start_patches, stop_patches

ROW_ID, TEAM, USER = 5, 456, 1
HUB_A, HUB_B, TEAM2 = 111, 222, 333
ADMIN_ROLE = 50


def _role(i, name, position, managed=False, default=False):
    return SimpleNamespace(id=i, name=name, position=position, managed=managed,
                           is_default=lambda d=default: d)


def _guild(i, name, owner_id=99, roles=()):
    g = MagicMock()
    g.id, g.name, g.owner_id, g.roles = i, name, owner_id, list(roles)
    return g


def _member(roles=()):
    return SimpleNamespace(id=USER, roles=[SimpleNamespace(id=r) for r in roles])


@pytest.fixture
def web():
    team = _guild(TEAM, "Clone", owner_id=USER)
    team.get_member.side_effect = lambda i: _member() if i == USER else None
    hub_a = _guild(HUB_A, "Hub A", roles=[
        _role(HUB_A, "@everyone", 0, default=True),
        _role(7, "Bot", 9, managed=True),
        _role(ADMIN_ROLE, "mod", 8),
        _role(11, "Team Low", 1),
        _role(12, "Team High", 5),
        _role(13, "Organiser", 4),
        _role(14, "Organizers", 3),
    ])
    hub_b = _guild(HUB_B, "Hub B", roles=[_role(21, "X", 1)])
    team2 = _guild(TEAM2, "Other clone", roles=[_role(31, "Y", 1)])
    bot = MagicMock()
    bot.guilds = [team, hub_a, hub_b, team2]
    by_id = {g.id: g for g in bot.guilds}
    bot.get_guild.side_effect = by_id.get

    hub_roles = {HUB_A: [ADMIN_ROLE], HUB_B: []}

    async def hub_member(hub, uid):
        return _member(hub_roles.get(hub.id, [ADMIN_ROLE]))

    def tier_ids(guild, names):
        return {ADMIN_ROLE}

    def settings_for(snowflake):
        done = datetime.datetime(2026, 10, 1) if snowflake == TEAM2 else None
        return SimpleNamespace(id=snowflake, setup_complete_time=done)

    sdb = MagicMock()
    sdb.get.side_effect = settings_for
    hdb = MagicMock()
    hdb.live_team_hunt_for_guild.side_effect = (
        lambda s: SimpleNamespace(id=1) if s == TEAM2 else None)
    hdb.get_by_attr.return_value = None
    mdb = MagicMock()
    mdb.running_for_hub.return_value = []
    mdb.for_hunt.return_value = None

    cog = MagicMock()
    audit = AsyncMock()
    state = SimpleNamespace(audit=audit, mdb=mdb, 
        bot=bot, team=team, hub_a=hub_a, sdb=sdb, hdb=hdb, cog=cog,
        perform=AsyncMock(), send=AsyncMock(), problems=MagicMock(return_value=[]),
        roles=lambda: None)
    state.perform.return_value = SetupOutcome(
        lines=["• did x", ":white_check_mark: done"], ok=True,
        hunt=SimpleNamespace(uid="HU1"))
    state.send.return_value = "report"
    app = make_web_app("actions", cog=cog)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": USER, "username": "t"}}),
        patch("bot.web.actions.web_data.get_guilds_map", AsyncMock(return_value={
            ROW_ID: {"guild_snowflake": TEAM, "guild_name": "Clone"}})),
        patch("bot.web.actions.bot_app", return_value=SimpleNamespace(bot=bot)),
        patch("bot.web.actions.puzzles_cog", return_value=cog),
        patch("bot.web.actions.GuildSettingsDb", sdb),
        patch("bot.web.actions.HuntJsonDb", hdb),
        patch("bot.web.actions.MultiTeamDb", mdb),
        patch("bot.web.actions._audit", audit),
        patch("bot.core.role_gate.tier_role_ids", tier_ids),
        patch("bot.core.hunt_archive.hub_member", hub_member),
        patch("bot.core.server_setup.template_problems", state.problems),
        patch("bot.core.server_setup.perform_setup", state.perform),
        patch("bot.core.team_invites.send_invites", state.send),
        patch("bot.core.team_invites.report_text", lambda r: f"text:{r}"),
    ]
    start_patches(patches)
    try:
        state.client = app.test_client()
        state.hub_roles = hub_roles
        yield state
    finally:
        stop_patches(patches)


URL = f"/api/guilds/{ROW_ID}/setup"
BODY = {"hub_id": str(HUB_A), "roster_role_id": "12", "hunt_name": "MH",
        "hunt_url": "https://mh.example"}


async def test_owner_without_mod_can_set_up(web):
    resp = await web.client.get(URL)
    assert resp.status_code == 200
    resp = await web.client.post(URL, json=BODY)
    assert resp.status_code == 200


async def test_stranger_refused(web):
    web.team.owner_id = 2
    for resp in (await web.client.get(URL), await web.client.post(URL, json=BODY)):
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "not_admin"
    web.perform.assert_not_awaited()


async def test_admin_tier_non_owner_allowed(web):
    web.team.owner_id = 2
    web.team.get_member.side_effect = lambda i: _member([ADMIN_ROLE])
    assert (await web.client.get(URL)).status_code == 200


async def test_not_a_member_refused(web):
    web.team.get_member.side_effect = lambda i: None
    web.team.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    resp = await web.client.get(URL)
    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "not_a_member"


async def test_get_lists_only_hubs_where_viewer_is_admin(web):
    body = await (await web.client.get(URL)).get_json()
    assert [h["id"] for h in body["hubs"]] == [str(HUB_A)]
    assert body["hubs"][0]["name"] == "Hub A"


async def test_get_roles_exclude_everyone_managed_admin_and_organiser_roles(web):
    body = await (await web.client.get(URL)).get_json()
    assert body["hubs"][0]["roles"] == [
        {"id": "12", "name": "Team High"}, {"id": "11", "name": "Team Low"}]


async def test_get_reports_template_problems(web):
    web.problems.return_value = ["drag the bot's role above `mod`"]
    body = await (await web.client.get(URL)).get_json()
    assert body["template_problems"] == ["drag the bot's role above `mod`"]
    assert body["already_set_up"] is None and body["existing"] is None


async def test_get_prefills_existing(web):
    hunt = SimpleNamespace(id=9, uid="HU1", name="MH", url="https://mh.example",
                           roster_role_id=12)
    web.hdb.get_by_attr.return_value = hunt
    with patch("bot.core.role_panel.find_roster_role",
               return_value=SimpleNamespace(guild=SimpleNamespace(id=HUB_A))):
        body = await (await web.client.get(URL)).get_json()
    assert body["existing"] == {"hub_id": str(HUB_A), "roster_role_id": "12",
                                "hunt_name": "MH", "hunt_url": "https://mh.example",
                                "multi_team": None}
    web.hdb.get_by_attr.assert_called_with(guild_id=ROW_ID)


async def test_get_reports_already_set_up(web):
    web.sdb.get.side_effect = lambda s: SimpleNamespace(
        id=s, setup_complete_time=datetime.datetime(2026, 10, 2, 3, 4, 5))
    body = await (await web.client.get(URL)).get_json()
    assert body["already_set_up"] == "2026-10-02T03:04:05"


async def test_post_runs_perform_setup_with_fields(web):
    body = dict(BODY, start_timestamp=1700000000, username="u", password="p",
                create_solved_sheet=True)
    resp = await web.client.post(URL, json=body)
    assert resp.status_code == 200
    out = await resp.get_json()
    assert out == {"ok": True, "lines": ["• did x", ":white_check_mark: done"],
                   "error": None, "hunt_uid": "HU1", "invites": None}
    args, kwargs = web.perform.await_args
    assert args[0] is web.bot and args[1] is web.cog.service and args[2] is web.team
    assert args[3].id == USER
    assert args[4:] == (HUB_A, 12, "MH", "https://mh.example")
    assert kwargs == {"start_timestamp": 1700000000, "username": "u", "password": "p",
                      "create_solved_sheet": True, "sheet_mode": "tabs",
                      "multi_team": False, "invites_by_dashboard": False,
                      "via_dashboard": True}


async def test_retry_finishes_a_partial_setup(web):
    web.perform.side_effect = [
        SetupOutcome(lines=["• made role", ":x: Setup stopped: boom"], ok=False,
                     hunt=SimpleNamespace(uid="HU1"), error="boom"),
        SetupOutcome(lines=["• made channel", ":white_check_mark: done"], ok=True,
                     hunt=SimpleNamespace(uid="HU1")),
    ]
    first = await web.client.post(URL, json=BODY)
    assert first.status_code == 200
    out = await first.get_json()
    assert out["ok"] is False and out["error"] == "boom"
    assert out["lines"] == ["• made role", ":x: Setup stopped: boom"]
    second = await (await web.client.post(URL, json=BODY)).get_json()
    assert second["ok"] is True and second["error"] is None
    assert web.perform.await_count == 2


async def test_send_invites_after_success(web):
    out = await (await web.client.post(URL, json=dict(BODY, send_invites=True))).get_json()
    web.send.assert_awaited_once()
    assert web.send.await_args.args == (web.bot, web.team, web.perform.return_value.hunt)
    assert out["invites"] == "text:report"


async def test_send_invites_tells_perform_setup_invites_went_out(web):
    await web.client.post(URL, json=dict(BODY, send_invites=True))
    kwargs = web.perform.await_args.kwargs
    assert kwargs["invites_by_dashboard"] is True and kwargs["via_dashboard"] is True


async def test_send_invites_failure_is_reported_not_raised(web):
    web.send.side_effect = RuntimeError("discord down")
    resp = await web.client.post(URL, json=dict(BODY, send_invites=True))
    assert resp.status_code == 200
    out = await resp.get_json()
    assert out["ok"] is True and out["lines"] == ["• did x", ":white_check_mark: done"]
    assert out["invites"] == ("Setup finished, but sending invites failed — the error has "
                              "been logged. Run !send_invites MH in #bot-commands.")
    web.audit.assert_awaited_once()


async def test_get_reports_hunt_uid(web):
    assert (await (await web.client.get(URL)).get_json())["hunt_uid"] is None
    web.hdb.get_by_attr.return_value = SimpleNamespace(
        id=9, uid="HU9", name="MH", url="u", roster_role_id=None)
    assert (await (await web.client.get(URL)).get_json())["hunt_uid"] == "HU9"


async def test_send_invites_skipped_when_setup_errored(web):
    web.perform.return_value = SetupOutcome(lines=[":x:"], ok=False, error="boom",
                                            hunt=SimpleNamespace(uid="HU1"))
    out = await (await web.client.post(URL, json=dict(BODY, send_invites=True))).get_json()
    web.send.assert_not_awaited()
    assert out["invites"] is None and out["ok"] is False


async def test_send_invites_off_by_default(web):
    await web.client.post(URL, json=BODY)
    web.send.assert_not_awaited()


async def test_bad_keys_refused(web):
    resp = await web.client.post(URL, json=dict(BODY, nope=1))
    assert resp.status_code == 400
    assert await resp.get_json() == {"error": "bad_keys", "unknown": ["nope"]}
    web.perform.assert_not_awaited()


async def test_missing_and_bad_types_refused(web):
    resp = await web.client.post(URL, json={"hub_id": str(HUB_A)})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "missing_keys"
    for patch_ in ({"hub_id": "abc"}, {"roster_role_id": True}, {"hunt_name": 5},
                   {"start_timestamp": "x"}, {"start_timestamp": 1e300},
                   {"create_solved_sheet": "yes"}):
        resp = await web.client.post(URL, json=dict(BODY, **patch_))
        assert resp.status_code == 400, patch_
    web.perform.assert_not_awaited()


async def test_float_start_timestamp_passed_as_int(web):
    resp = await web.client.post(URL, json=dict(BODY, start_timestamp=1700000000.7))
    assert resp.status_code == 200
    passed = web.perform.await_args.kwargs["start_timestamp"]
    assert passed == 1700000000 and type(passed) is int


async def test_sheet_mode_files_is_passed_through(web):
    resp = await web.client.post(URL, json=dict(BODY, sheet_mode="files"))
    assert resp.status_code == 200
    assert web.perform.await_args.kwargs["sheet_mode"] == "files"


async def test_bad_sheet_mode_refused(web):
    resp = await web.client.post(URL, json=dict(BODY, sheet_mode="bogus"))
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "bad_sheet_mode"
    web.perform.assert_not_awaited()


async def test_files_with_solved_sheet_refused_before_setup(web):
    resp = await web.client.post(
        URL, json=dict(BODY, sheet_mode="files", create_solved_sheet=True))
    assert resp.status_code == 409
    assert (await resp.get_json())["error"] == "files_mode_no_solved_sheet"
    web.perform.assert_not_awaited()


async def test_get_lists_running_multi_team_hunts_per_hub(web):
    web.mdb.running_for_hub.side_effect = lambda hub_id: (
        [{"id": 3, "name": "GPH 2026", "team_count": 2},
         {"id": 4, "name": "Other", "team_count": 1}] if hub_id == HUB_A else [])
    data = await (await web.client.get(URL)).get_json()
    hub = next(h for h in data["hubs"] if h["id"] == str(HUB_A))
    assert hub["multi_team_hunts"] == [{"id": 3, "name": "GPH 2026", "team_count": 2},
                                       {"id": 4, "name": "Other", "team_count": 1}]


async def test_get_existing_reports_the_linked_multi_team_hunt(web):
    web.hdb.get_by_attr.return_value = SimpleNamespace(
        id=9, name="MH", url="u", roster_role_id=0, uid="HU9")
    web.mdb.for_hunt.return_value = {"multi_team_id": 3, "name": "GPH 2026"}
    data = await (await web.client.get(URL)).get_json()
    assert data["existing"]["multi_team"] == {"id": 3, "name": "GPH 2026"}
    web.mdb.for_hunt.return_value = None
    data = await (await web.client.get(URL)).get_json()
    assert data["existing"]["multi_team"] is None


async def test_post_passes_multi_team_through(web):
    assert (await web.client.post(URL, json={**BODY, "multi_team": True})).status_code == 200
    assert web.perform.await_args.kwargs["multi_team"] is True
    await web.client.post(URL, json=BODY)
    assert web.perform.await_args.kwargs["multi_team"] is False


@pytest.mark.parametrize("bad", ["yes", 1, None, "true"])
async def test_post_rejects_a_non_bool_multi_team(web, bad):
    resp = await web.client.post(URL, json={**BODY, "multi_team": bad})
    assert resp.status_code == 400
    assert await resp.get_json() == {"error": "bad_value", "field": "multi_team"}
    web.perform.assert_not_awaited()

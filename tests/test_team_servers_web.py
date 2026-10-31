"""Team servers API: GET /api/guilds/<id>/team-hunts[/<uid>]."""
import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_web_app, start_patches, stop_patches

GUILD_ROW_ID, HUB, TEAM_SNOW = 1, 123, 456
ROSTER, TEAM_ROLE, MOD = 100, 60, 50
SECRET = "SECRETCODE"


def _hunt(id_, uid, name, archived=False, roster=ROSTER, role_id=TEAM_ROLE):
    return SimpleNamespace(id=id_, uid=uid, name=name, archived=archived,
                           roster_role_id=roster, role_id=role_id,
                           mod_invite_code=SECRET)


def _member(i, name, roles=(), bot=False):
    return SimpleNamespace(id=i, display_name=name, bot=bot,
                           roles=[SimpleNamespace(id=r) for r in roles])


def _dt(day):
    return datetime.datetime(2026, 10, day, 8, 0, 0)


@pytest.fixture
def web():
    hub = MagicMock()
    hub.name = "Hub"
    hub.get_role.side_effect = (
        lambda i: SimpleNamespace(id=i, name="Team Rocket") if i == ROSTER else None)
    hub.get_member.side_effect = lambda i: _member(i, "Modly") if i == 777 else None

    team = MagicMock()
    team.name = "Rocket HQ"
    team.members = [_member(1, "fay", [TEAM_ROLE]), _member(2, "bo"),
                    _member(3, "bot", bot=True), _member(4, "mo", [MOD])]
    team.get_role.side_effect = lambda i: (
        SimpleNamespace(id=i) if i in (TEAM_ROLE, MOD) else None)
    team.get_member.side_effect = lambda i: next((m for m in team.members if m.id == i), None)

    bot = MagicMock()
    guilds = {HUB: hub, TEAM_SNOW: team}
    bot.get_guild.side_effect = lambda g: guilds.get(g)

    running = _hunt(10, "RUN", "Running Hunt")
    archived = _hunt(11, "OLD", "Old Hunt", archived=True)
    foreign = _hunt(12, "FOREIGN", "Elsewhere", roster=999)
    rows = [(running, TEAM_SNOW), (foreign, 789), (archived, HUB)]
    hdb = MagicMock()
    hdb.team_hunts.return_value = rows
    hdb.get_by_attr.side_effect = lambda uid: next(
        (h for h, _ in rows if h.uid == uid), None)
    idb = MagicMock()
    idb.for_hunt.side_effect = lambda hid: [
        {"user_id": 2, "created_at": _dt(1), "joined_at": _dt(2)},
        {"user_id": 5, "created_at": _dt(3), "joined_at": None, "code": SECRET},
    ] if hid == 10 else []
    ldb = MagicMock()
    ldb.for_hunt.side_effect = lambda hid: [
        {"id": 1, "created_at": _dt(1), "hunt_id": hid, "user_id": 1, "username": "fay",
         "action": "grant", "via": "arrival", "actor_id": None, "detail": None},
        {"id": 2, "created_at": _dt(2), "hunt_id": hid, "user_id": 4, "username": "mo",
         "action": "elevate", "via": "hand", "actor_id": 777, "detail": None},
    ]

    app = make_web_app("actions", cog=MagicMock())
    state = SimpleNamespace(
        require_admin=AsyncMock(return_value=None), hub=hub, team=team, bot=bot,
        hdb=hdb, running=running)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.require_admin", state.require_admin),
        patch("bot.web.actions.web_data.get_guilds_map", AsyncMock(return_value={
            GUILD_ROW_ID: {"guild_snowflake": HUB, "guild_name": "Hub"}})),
        patch("bot.web.actions.GuildSettingsDb", MagicMock(get=MagicMock(return_value=object()))),
        patch("bot.web.actions.bot_app", return_value=SimpleNamespace(bot=bot)),
        patch("bot.web.actions.HuntJsonDb", hdb),
        patch("bot.web.actions.HuntInviteDb", idb),
        patch("bot.web.actions.HuntAccessLogDb", ldb),
        patch("bot.core.role_gate.tier_role_ids", return_value={MOD}),
    ]
    start_patches(patches)
    try:
        state.client = app.test_client()
        yield state
    finally:
        stop_patches(patches)


URL = f"/api/guilds/{GUILD_ROW_ID}/team-hunts"


async def test_list_running_and_archived(web):
    resp = await web.client.get(URL)
    assert resp.status_code == 200
    hunts = (await resp.get_json())["hunts"]
    assert [h["uid"] for h in hunts] == ["RUN", "OLD"]
    assert hunts[0] == {"uid": "RUN", "name": "Running Hunt", "team": "Team Rocket",
                        "archived": False, "team_server": "Rocket HQ",
                        "on_server_count": 3, "invited_waiting_count": 1}
    assert hunts[1]["archived"] is True
    assert hunts[1]["team_server"] is None and hunts[1]["on_server_count"] is None


async def test_list_empty(web):
    web.hdb.team_hunts.return_value = []
    resp = await web.client.get(URL)
    assert resp.status_code == 200
    assert await resp.get_json() == {"hunts": []}


async def test_list_requires_admin(web):
    web.require_admin.return_value = ({"error": "forbidden"}, 403)
    resp = await web.client.get(URL)
    assert resp.status_code == 403
    resp = await web.client.get(URL + "/RUN")
    assert resp.status_code == 403


async def test_detail_running(web):
    resp = await web.client.get(URL + "/RUN")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["hunt"]["uid"] == "RUN" and body["hunt"]["on_server_count"] == 3
    assert body["bot_on_server"] is True and body["team_role_missing"] is False
    assert [(m["name"], m["team_role"], m["mod"]) for m in body["on_server"]] == [
        ("mo", False, True), ("fay", True, False), ("bo", False, False)]
    assert body["invited_waiting"] == [
        {"user_id": "5", "name": "5", "invited_at": "2026-10-03T08:00:00Z"}]
    assert [h["action"] for h in body["history"]] == ["elevate", "grant"]
    assert body["history"][0]["actor_name"] == "Modly"
    assert body["history"][0]["actor_id"] == "777"


async def test_detail_archived_has_no_on_server(web):
    resp = await web.client.get(URL + "/OLD")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["on_server"] is None
    assert body["hunt"]["archived"] is True
    assert len(body["history"]) == 2


async def test_detail_refuses_a_hunt_from_another_hub(web):
    resp = await web.client.get(URL + "/FOREIGN")
    assert resp.status_code == 404
    assert await resp.get_json() == {"error": "hunt_not_found"}
    resp = await web.client.get(URL + "/NOPE")
    assert resp.status_code == 404


async def test_detail_when_the_bot_left_the_team_server(web):
    web.bot.get_guild.side_effect = lambda g: web.hub if g == HUB else None
    resp = await web.client.get(URL + "/RUN")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["bot_on_server"] is False and body["on_server"] is None
    assert body["hunt"]["team_server"] is None
    assert [w["user_id"] for w in body["invited_waiting"]] == ["5"]
    assert len(body["history"]) == 2


async def test_no_invite_codes_in_any_response(web):
    # the store never selects the code; even rows that carried one must not leak
    web.running.mod_invite_code = SECRET
    texts = []
    for path in (URL, URL + "/RUN"):
        resp = await web.client.get(path)
        texts.append(json.dumps(await resp.get_json()))
    assert all(SECRET not in t for t in texts)

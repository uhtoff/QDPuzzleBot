"""Mark-complete entrypoints for multi-team hunts: !hunt_complete / !hunt_not_complete,
POST /api/hunts/<uid>/complete, POST /api/guilds/<id>/team-hunts/<uid>/complete, and
the `multi_team` payloads. Helpers are uniquely named and local (the parallel runner
shards by file)."""
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import multi_team
from tests.conftest import (make_cog, make_ctx, make_hunt, make_hunt_ctx,
                            make_web_app, start_patches, stop_patches)

OUTCOME = multi_team.CompleteOutcome(changed=True, message="Marked **Rocket** finished.")
HUB, HUB_ROW, ROSTER = 123, 1, 100


# ------------------------------------------------------------------ commands
def _mt_cog(hunt, channel_type="Hunt"):
    cog = make_cog(hunt=hunt)
    cog.get_channel_type = lambda ctx: channel_type
    cog.bot = MagicMock()
    return cog


def _mt_no_mentions(call):
    am = call.kwargs.get("allowed_mentions")
    return am is not None and (am.everyone, am.users, am.roles) == (False, False, False)


def _mt_said(ctx):
    return "\n".join(c.args[0] for c in ctx.send.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,flag", [("hunt_complete", True), ("hunt_not_complete", False)])
async def test_command_relays_outcome_and_passes_the_right_flag(name, flag):
    from bot.cogs.puzzles import Puzzles
    hunt = make_hunt()
    cog, ctx = _mt_cog(hunt), make_ctx()
    with patch("bot.cogs.puzzles.multi_team.set_complete",
               AsyncMock(return_value=OUTCOME)) as sc:
        await getattr(Puzzles, name).callback(cog, ctx)
    sc.assert_awaited_once_with(cog.bot, hunt, flag)
    assert _mt_said(ctx) == OUTCOME.message
    assert all(_mt_no_mentions(c) for c in ctx.send.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["hunt_complete", "hunt_not_complete"])
async def test_command_outside_hunt_channel_is_refused(name):
    from bot.cogs.puzzles import Puzzles
    cog, ctx = _mt_cog(make_hunt(), channel_type="Puzzle"), make_ctx()
    with patch("bot.cogs.puzzles.multi_team.set_complete", AsyncMock()) as sc:
        await getattr(Puzzles, name).callback(cog, ctx)
    sc.assert_not_awaited()
    assert _mt_said(ctx) == ":x: This command must be done from the main Hunt channel"
    assert all(_mt_no_mentions(c) for c in ctx.send.await_args_list)


@pytest.mark.asyncio
async def test_command_relays_a_refusal():
    from bot.cogs.puzzles import Puzzles
    cog, ctx = _mt_cog(make_hunt()), make_ctx()
    with patch("bot.cogs.puzzles.multi_team.set_complete",
               AsyncMock(side_effect=multi_team.MultiTeamRefused("Not a multi-team hunt."))):
        await Puzzles.hunt_complete.callback(cog, ctx)
    assert _mt_said(ctx) == ":x: Not a multi-team hunt."
    assert all(_mt_no_mentions(c) for c in ctx.send.await_args_list)


@pytest.mark.asyncio
async def test_command_logs_and_reports_an_unexpected_error():
    from bot.cogs.puzzles import Puzzles
    cog, ctx = _mt_cog(make_hunt()), make_ctx()
    with patch("bot.cogs.puzzles.multi_team.set_complete",
               AsyncMock(side_effect=RuntimeError("boom secret"))):
        await Puzzles.hunt_not_complete.callback(cog, ctx)
    said = _mt_said(ctx)
    assert said == ":x: Couldn't update the hub channels — the error has been logged."
    assert "boom" not in said
    assert all(_mt_no_mentions(c) for c in ctx.send.await_args_list)


# ------------------------------------------------------------- team-hunt route
@pytest.fixture
def hubweb():
    hub = MagicMock()
    hub.get_role.side_effect = (
        lambda i: SimpleNamespace(id=i, name="Team Rocket") if i == ROSTER else None)
    bot = MagicMock()
    hunt = make_hunt(id=10, uid="RUN", name="Rocket", roster_role_id=ROSTER, archived=False)
    foreign = make_hunt(id=12, uid="FOR", name="Else", roster_role_id=999, archived=False)
    hdb = MagicMock()
    hdb.get_by_attr.side_effect = lambda uid: {"RUN": hunt, "FOR": foreign}.get(uid)
    state = SimpleNamespace(require_admin=AsyncMock(return_value=None), hunt=hunt,
                            bot=bot, audit=AsyncMock(), notify=MagicMock())
    app = make_web_app("actions", cog=MagicMock())
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.require_admin", state.require_admin),
        patch("bot.web.actions.web_data.get_guilds_map", AsyncMock(return_value={
            HUB_ROW: {"guild_snowflake": HUB, "guild_name": "Hub"}})),
        patch("bot.web.actions.GuildSettingsDb", MagicMock(get=MagicMock(return_value=object()))),
        patch("bot.web.actions.bot_app", return_value=SimpleNamespace(bot=bot)),
        patch("bot.web.actions.HuntJsonDb", hdb),
        patch("bot.web.actions._audit", state.audit),
        patch("bot.web.actions._notify_board", state.notify),
    ]
    start_patches(patches)
    bot.get_guild.side_effect = lambda g: hub if g == HUB else None
    try:
        state.client = app.test_client()
        yield state
    finally:
        stop_patches(patches)


HUB_URL = f"/api/guilds/{HUB_ROW}/team-hunts/RUN/complete"


@pytest.mark.asyncio
async def test_hub_route_requires_admin(hubweb):
    hubweb.require_admin.return_value = ({"error": "forbidden"}, 403)
    with patch("bot.web.actions.multi_team.set_complete", AsyncMock()) as sc:
        resp = await hubweb.client.post(HUB_URL, json={"complete": True})
    assert resp.status_code == 403
    sc.assert_not_awaited()


@pytest.mark.asyncio
async def test_hub_route_unknown_or_foreign_hunt_is_404(hubweb):
    with patch("bot.web.actions.multi_team.set_complete", AsyncMock()) as sc:
        for uid in ("NOPE", "FOR"):
            resp = await hubweb.client.post(
                f"/api/guilds/{HUB_ROW}/team-hunts/{uid}/complete", json={"complete": True})
            assert resp.status_code == 404, uid
            assert (await resp.get_json())["error"] == "hunt_not_found"
    sc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"complete": "yes"}, {"complete": 1}, {"complete": None}])
async def test_hub_route_bad_body_is_400(hubweb, body):
    with patch("bot.web.actions.multi_team.set_complete", AsyncMock()) as sc:
        resp = await hubweb.client.post(HUB_URL, json=body)
    assert resp.status_code == 400
    assert await resp.get_json() == {"error": "bad_value", "field": "complete"}
    sc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_hub_route_success_passes_the_flag(hubweb, flag):
    with patch("bot.web.actions.multi_team.set_complete",
               AsyncMock(return_value=OUTCOME)) as sc:
        resp = await hubweb.client.post(HUB_URL, json={"complete": flag})
    assert resp.status_code == 200
    assert await resp.get_json() == {"ok": True, "changed": True, "message": OUTCOME.message}
    sc.assert_awaited_once_with(hubweb.bot, hubweb.hunt, flag)
    assert hubweb.audit.await_args.args[0] == "hunt_complete"
    assert hubweb.audit.await_args.kwargs["detail"] == f"complete={flag} changed=True"


@pytest.mark.asyncio
async def test_hub_route_refusal_is_409_with_message(hubweb):
    with patch("bot.web.actions.multi_team.set_complete",
               AsyncMock(side_effect=multi_team.MultiTeamRefused("Nope."))):
        resp = await hubweb.client.post(HUB_URL, json={"complete": True})
    assert resp.status_code == 409
    assert await resp.get_json() == {"error": "refused", "message": "Nope."}


@pytest.mark.asyncio
async def test_hub_route_unexpected_error_is_502(hubweb):
    with patch("bot.web.actions.multi_team.set_complete",
               AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await hubweb.client.post(HUB_URL, json={"complete": True})
    assert resp.status_code == 502
    assert await resp.get_json() == {"error": "hub_update_failed"}


# ------------------------------------------------------------------ hunt route
@pytest.fixture
def huntweb():
    hunt = make_hunt(id=10, uid="U1", name="H")
    hdb = MagicMock()
    hdb.get_by_attr.return_value = hunt
    bot = MagicMock()
    state = SimpleNamespace(require_admin=AsyncMock(return_value=None), hunt=hunt,
                            bot=bot, audit=AsyncMock(), notify=MagicMock())
    app = make_web_app("actions", cog=MagicMock())
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.require_admin", state.require_admin),
        patch("bot.web.actions._visible_hunt_by_uid",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions.bot_app", return_value=SimpleNamespace(bot=bot)),
        patch("bot.web.actions._audit", state.audit),
        patch("bot.web.actions._notify_board", state.notify),
    ]
    start_patches(patches)
    try:
        state.client = app.test_client()
        yield state
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_hunt_route_requires_admin(huntweb):
    huntweb.require_admin.return_value = ({"error": "forbidden"}, 403)
    with patch("bot.web.actions.multi_team.set_complete", AsyncMock()) as sc:
        resp = await huntweb.client.post("/api/hunts/U1/complete", json={"complete": True})
    assert resp.status_code == 403
    sc.assert_not_awaited()
    huntweb.require_admin.assert_awaited_once_with("123")


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"complete": "yes"}, {"complete": 1}, {"complete": None}])
async def test_hunt_route_bad_body_is_400(huntweb, body):
    with patch("bot.web.actions.multi_team.set_complete", AsyncMock()) as sc:
        resp = await huntweb.client.post("/api/hunts/U1/complete", json=body)
    assert resp.status_code == 400
    assert await resp.get_json() == {"error": "bad_value", "field": "complete"}
    sc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_hunt_route_success_passes_the_flag(huntweb, flag):
    with patch("bot.web.actions.multi_team.set_complete",
               AsyncMock(return_value=OUTCOME)) as sc:
        resp = await huntweb.client.post("/api/hunts/U1/complete", json={"complete": flag})
    assert resp.status_code == 200
    assert await resp.get_json() == {"ok": True, "changed": True, "message": OUTCOME.message}
    sc.assert_awaited_once_with(huntweb.bot, huntweb.hunt, flag)
    assert huntweb.audit.await_args.args[0] == "hunt_complete"
    assert huntweb.audit.await_args.kwargs["detail"] == f"complete={flag} changed=True"
    huntweb.notify.assert_called_once()


@pytest.mark.asyncio
async def test_hunt_route_refusal_is_409_with_message(huntweb):
    with patch("bot.web.actions.multi_team.set_complete",
               AsyncMock(side_effect=multi_team.MultiTeamRefused("Nope."))):
        resp = await huntweb.client.post("/api/hunts/U1/complete", json={"complete": False})
    assert resp.status_code == 409
    assert await resp.get_json() == {"error": "refused", "message": "Nope."}


@pytest.mark.asyncio
async def test_hunt_route_unexpected_error_is_502(huntweb):
    with patch("bot.web.actions.multi_team.set_complete",
               AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await huntweb.client.post("/api/hunts/U1/complete", json={"complete": True})
    assert resp.status_code == 502
    assert await resp.get_json() == {"error": "hub_update_failed"}


# -------------------------------------------------------------------- payloads
def _mt_link(completed, archived=None):
    return {"multi_team_id": 7, "name": "Mystery 2027",
            "completed_time": datetime.datetime(2026, 10, 1) if completed else None,
            "archived_time": datetime.datetime(2026, 10, 2) if archived else None}


async def _detail_payload(link):
    app = make_web_app("api", cog=MagicMock())
    bot_app = MagicMock()
    bot_app.return_value.bot.get_guild.return_value = None
    row = {"id": 10, "uid": "U1", "name": "H", "url": "u", "archived": False,
           "guild_id": 1, "role_id": 0, "roster_role_id": ROSTER,
           "google_sheet_id": None, "solved_sheet_id": None, "sheet_mode": "tabs"}
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
        patch("bot.web.api.MultiTeamDb", MagicMock(for_hunt=MagicMock(return_value=link))),
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().get("/api/hunts/U1")
        return (await resp.get_json())["hunt"]
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
@pytest.mark.parametrize("completed,archived", [(True, False), (False, False), (True, True)])
async def test_hunt_payload_carries_multi_team(completed, archived):
    hunt = await _detail_payload(_mt_link(completed, archived=archived))
    assert hunt["multi_team"] == {"name": "Mystery 2027", "completed": completed,
                                  "archived": archived}


@pytest.mark.asyncio
async def test_hunt_payload_multi_team_is_null_when_unlinked():
    hunt = await _detail_payload(None)
    assert "multi_team" in hunt and hunt["multi_team"] is None


def test_team_hunt_out_multi_team_shapes():
    from bot.web.actions import _team_hunt_out
    hub = MagicMock()
    hub.get_role.return_value = SimpleNamespace(name="Team Rocket")
    hunt = make_hunt(id=10, uid="RUN", name="R", roster_role_id=ROSTER, archived=False)
    bot = MagicMock()
    cases = [
        (_mt_link(False), {"id": 7, "name": "Mystery 2027", "completed": False, "archived": False}),
        (_mt_link(True, archived=True),
         {"id": 7, "name": "Mystery 2027", "completed": True, "archived": True}),
        (None, None),
    ]
    for link, expected in cases:
        with patch("bot.web.actions.MultiTeamDb", MagicMock(for_hunt=MagicMock(return_value=link))):
            out, _ = _team_hunt_out(hub, hunt, None, bot)
        assert out["multi_team"] == expected

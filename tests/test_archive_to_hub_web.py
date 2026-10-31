"""POST /api/hunts/<uid>/archive-to-hub and the in-place archive's team-hunt refusal."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hunt_archive
from bot.core.hub_archive_run import RUNNING_HUB_ARCHIVES
from tests.conftest import (make_hunt, make_hunt_ctx, make_web_app,
                            start_patches, stop_patches)

URL = "/api/hunts/U1/archive-to-hub"


def _web(*, archived=False, roster_role_id=70, layout_mode="channel", roster_found=True,
         hub_error=None, member=True, runner=None):
    cog = MagicMock()
    team_guild = MagicMock(); team_guild.id = 5
    team_guild.get_member.return_value = MagicMock() if member else None
    team_guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    app = make_web_app("actions", cog=cog, get_guild=team_guild)
    hunt = make_hunt(name="Big Hunt", roster_role_id=roster_role_id, layout_mode=layout_mode,
                     archived=archived)
    hdb = MagicMock(); hdb.get_by_attr.return_value = hunt
    hub = MagicMock(); hub.id = 900
    role = MagicMock(); role.guild.id = 900
    resolve = AsyncMock(side_effect=hub_error) if hub_error else AsyncMock(return_value=(hub, MagicMock()))
    admin_ch = MagicMock(); admin_ch.name = "bot-admin"
    run = runner or AsyncMock()
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 42, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid",
              AsyncMock(return_value=(make_hunt_ctx(archived=archived), None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.core.role_panel.find_roster_role", return_value=role if roster_found else None),
        patch("bot.core.hunt_archive.resolve_hub", resolve),
        patch("bot.core.hub_channel.find", return_value=(admin_ch, "")),
        patch("bot.core.hub_archive_run.run_archive_to_hub", run),
    ]
    start_patches(patches)
    return SimpleNamespace(client=app.test_client(), patches=patches, hunt=hunt, run=run,
                           resolve=resolve, cog=cog, hub=hub, admin_ch=admin_ch)


@pytest.fixture(autouse=True)
def _clear_running():
    RUNNING_HUB_ARCHIVES.clear()
    yield
    RUNNING_HUB_ARCHIVES.clear()


async def _post(web, body):
    try:
        resp = await web.client.post(URL, json=body)
        return resp, await resp.get_json()
    finally:
        stop_patches(web.patches)


async def test_archive_to_hub_starts_and_returns_202():
    done = asyncio.Event()
    run = AsyncMock(side_effect=lambda *a, **k: done.set())
    web = _web(runner=run)
    resp, body = await _post(web, {"confirm_name": "Big Hunt"})
    assert resp.status_code == 202
    assert body == {"started": True, "progress_channel": "bot-admin"}
    await asyncio.wait_for(done.wait(), 2)
    await asyncio.sleep(0)
    run.assert_awaited_once()
    assert run.await_args.kwargs["status_channel"] is web.admin_ch
    assert run.await_args.args[1] is web.cog.service
    assert web.hunt.id not in RUNNING_HUB_ARCHIVES


async def test_confirm_mismatch_refused():
    web = _web()
    resp, body = await _post(web, {"confirm_name": "big hunt"})
    assert resp.status_code == 400 and body["error"] == "confirm_mismatch"
    web.run.assert_not_awaited()
    web.resolve.assert_not_awaited()


@pytest.mark.parametrize("kwargs, status, error", [
    ({"roster_role_id": 0}, 400, "not_a_team_hunt"),
    ({"layout_mode": "forum"}, 400, "forum_not_supported"),
    ({"roster_found": False}, 409, "hub_role_missing"),
    ({"archived": True}, 403, "hunt_archived"),
    ({"member": False}, 403, "not_a_member"),
])
async def test_non_team_hunt_refused(kwargs, status, error):
    web = _web(**kwargs)
    resp, body = await _post(web, {"confirm_name": "Big Hunt"})
    assert (resp.status_code, body["error"]) == (status, error)
    web.run.assert_not_awaited()


async def test_hub_unavailable_message_passes_through():
    web = _web(hub_error=hunt_archive.HubUnavailable("you aren't an admin on The Hub"))
    resp, body = await _post(web, {"confirm_name": "Big Hunt"})
    assert resp.status_code == 403
    assert body == {"error": "hub_unavailable", "message": "you aren't an admin on The Hub"}
    web.run.assert_not_awaited()
    assert 10 not in RUNNING_HUB_ARCHIVES


async def test_second_archive_while_running_is_refused():
    gate = asyncio.Event()

    async def slow(*a, **k):
        await gate.wait()

    web = _web(runner=AsyncMock(side_effect=slow))
    try:
        r1 = await web.client.post(URL, json={"confirm_name": "Big Hunt"})
        assert r1.status_code == 202
        r2 = await web.client.post(URL, json={"confirm_name": "Big Hunt"})
        assert r2.status_code == 409
        assert (await r2.get_json())["error"] == "archive_running"
    finally:
        gate.set()
        await asyncio.sleep(0.01)
        stop_patches(web.patches)
    assert 10 not in RUNNING_HUB_ARCHIVES


# --- the in-place archive route -------------------------------------------

def _inplace(*, roster_role_id, hunt_archived):
    app = make_web_app("actions", cog=MagicMock())
    hunt = make_hunt(roster_role_id=roster_role_id, archived=hunt_archived)
    hdb = MagicMock(); hdb.get_by_attr.return_value = hunt
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_by_uid",
              AsyncMock(return_value=(make_hunt_ctx(archived=hunt_archived), None))),
        patch("bot.web.actions.require_admin", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions._notify_board", MagicMock()),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    start_patches(patches)
    return SimpleNamespace(client=app.test_client(), patches=patches, hunt=hunt, hdb=hdb)


async def test_in_place_archive_refuses_team_hunt():
    web = _inplace(roster_role_id=70, hunt_archived=False)
    try:
        resp = await web.client.post("/api/hunts/U1/archive", json={"archived": True})
        body = await resp.get_json()
    finally:
        stop_patches(web.patches)
    assert resp.status_code == 409 and body["error"] == "use_hub_archive"
    web.hdb.commit.assert_not_called()
    assert web.hunt.archived is False


async def test_unarchive_unchanged():
    web = _inplace(roster_role_id=70, hunt_archived=True)
    try:
        resp = await web.client.post("/api/hunts/U1/archive", json={"archived": False})
        body = await resp.get_json()
    finally:
        stop_patches(web.patches)
    assert resp.status_code == 200 and body == {"ok": True, "archived": False}
    assert web.hunt.archived is False
    web.hdb.commit.assert_called_once()


async def test_in_place_archive_of_a_plain_hunt_still_works():
    web = _inplace(roster_role_id=0, hunt_archived=False)
    try:
        resp = await web.client.post("/api/hunts/U1/archive", json={"archived": True})
    finally:
        stop_patches(web.patches)
    assert resp.status_code == 200 and web.hunt.archived is True

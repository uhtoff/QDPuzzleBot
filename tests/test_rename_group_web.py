from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_hunt, make_round


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    cog.service.rename_group = AsyncMock()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}
    rdb = MagicMock()
    rdb.get_by_attr.return_value = make_round(id=5, name="Old")
    rdb.check_duplicates_in_hunt.return_value = False
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, rdb=rdb)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_rename_group_success(web):
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "New Name"})
    assert resp.status_code == 200
    assert (await resp.get_json())["ok"] is True
    web.cog.service.rename_group.assert_awaited_once()
    assert web.cog.service.rename_group.await_args.args[3] == "New Name"


@pytest.mark.asyncio
async def test_rename_group_empty_name_400(web):
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "  "})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "empty_name"
    web.cog.service.rename_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_group_duplicate_400(web):
    web.rdb.check_duplicates_in_hunt.return_value = True
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "Dupe"})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "duplicate_name"
    web.cog.service.rename_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_group_excludes_self_from_duplicate_check(web):
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "Old!"})
    assert resp.status_code == 200
    web.rdb.check_duplicates_in_hunt.assert_called_once()
    assert web.rdb.check_duplicates_in_hunt.call_args.kwargs.get("exclude_group_id") == 5


@pytest.mark.asyncio
async def test_rename_group_archived_403(web):
    web.ctx["hunt"]["archived"] = True
    resp = await web.client.post("/api/tag_groups/5/rename", json={"name": "New"})
    assert resp.status_code == 403

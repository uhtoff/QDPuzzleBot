from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_round, make_web_app,
                            start_patches, stop_patches)


@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.rename_group = AsyncMock()
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
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
    start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, rdb=rdb)
    finally:
        stop_patches(patches)


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

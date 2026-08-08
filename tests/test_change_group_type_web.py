from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_round, make_web_app,
                            start_patches, stop_patches)


@asynccontextmanager
async def _noop_lock(_ids):
    yield


@pytest.fixture
def web():
    cog = MagicMock()
    cog.service.convert_tag_group_type = AsyncMock(return_value=(make_round(id=5), "flipped 2 → none"))
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
    rdb = MagicMock(); rdb.get_by_attr.return_value = make_round(id=5, name="Old")
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()
    pdb = MagicMock(); pdb.get_all_from_tag_group.return_value = []

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.web.actions.puzzles_lock", _noop_lock),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(app=app, client=app.test_client(), cog=cog, ctx=ctx, rdb=rdb, pdb=pdb)
    finally:
        stop_patches(patches)


@pytest.mark.asyncio
async def test_change_type_calls_service(web):
    resp = await web.client.post("/api/tag_groups/5/change-type", json={"type": "Group"})
    assert resp.status_code == 200
    web.cog.service.convert_tag_group_type.assert_awaited_once()
    assert web.cog.service.convert_tag_group_type.await_args.args[3] == "Group"


@pytest.mark.asyncio
async def test_change_type_maps_round_alias(web):
    resp = await web.client.post("/api/tag_groups/5/change-type", json={"type": "Round"})
    assert resp.status_code == 200
    assert web.cog.service.convert_tag_group_type.await_args.args[3] == "Metapuzzle"


@pytest.mark.asyncio
async def test_change_type_rejects_unknown_type(web):
    resp = await web.client.post("/api/tag_groups/5/change-type", json={"type": "Bogus"})
    assert resp.status_code == 400
    web.cog.service.convert_tag_group_type.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_type_locks_target_and_members_in_one_acquisition():
    """Reversed by H5. The route used to lock only the *target*, leaving the members
    to bulk_re_edge's own nested acquisition — order `T, then members`, the inverse
    of the `sorted(members u {T})` the delete/archive edges take, which deadlocked
    the two against each other. The route now takes target + members in ONE sorted
    puzzles_lock and the service takes none.

    Uses REAL locks (no _noop_lock patch): a route that re-introduced a nested
    acquisition would hang and blow the wait_for timeout."""
    import asyncio

    tag_group = make_round(id=5, name="Old", target_puzzle_id=5)

    async def fake_convert(_guild, _hunt, tg, _new_type):
        # The service locks nothing; the route must already hold every member.
        from bot.core.locks import _PUZZLE_LOCKS
        assert _PUZZLE_LOCKS[5].locked() and _PUZZLE_LOCKS[6].locked()
        return (tg, "ok")

    cog = MagicMock()
    cog.service.convert_tag_group_type = fake_convert
    cog.service.refresh_unassigned_safe = AsyncMock()
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
    rdb = MagicMock(); rdb.get_by_attr.return_value = tag_group
    hdb = MagicMock(); hdb.get_by_attr.return_value = make_hunt()
    pdb = MagicMock()
    pdb.get_all_from_tag_group.return_value = [MagicMock(id=5), MagicMock(id=6)]

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    start_patches(patches)
    try:
        resp = await asyncio.wait_for(
            app.test_client().post("/api/tag_groups/5/change-type", json={"type": "Metapuzzle"}),
            timeout=5,
        )
        assert resp.status_code == 200
    finally:
        stop_patches(patches)

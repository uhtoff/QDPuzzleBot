"""Dashboard twins of !extract_target_to_category and !consolidate_to: the
extract preview, extract, and fold (consolidate) routes in bot/web/actions.py."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (make_hunt, make_hunt_ctx, make_round, make_web_app,
                            start_patches, stop_patches)


@asynccontextmanager
async def _noop_lock(_ids):
    yield


META = make_round(id=5, name="Spies", target_puzzle_id=50, category_id=5000)
DEST = make_round(id=6, name="Round One", target_puzzle_id=0, category_id=6000)
OTHER_HUNT = make_round(id=7, name="Elsewhere", hunt_id=99)
PLAIN = make_round(id=8, name="Grab bag", target_puzzle_id=0)


def _groups_by_id(**kw):
    return {5: META, 6: DEST, 7: OTHER_HUNT, 8: PLAIN}.get(kw.get("id", 0))


def _puzzle(id, name):
    p = MagicMock()
    p.id = id
    p.name = name
    return p


@pytest.fixture
def web():
    cog = MagicMock()
    new_category = MagicMock()
    new_category.name = "Spies"
    cog.service.extract_target_to_category = AsyncMock(return_value=new_category)
    cog.service.consolidate_to = AsyncMock(return_value=DEST)
    cog.service.extract_candidates = MagicMock(
        return_value=[_puzzle(50, "Spies (meta)"), _puzzle(51, "Mole")])
    cog.service.free_category_slots = MagicMock(return_value=3)
    app = make_web_app("actions", cog=cog)

    ctx = make_hunt_ctx()
    rdb = MagicMock()
    rdb.get_by_attr.side_effect = _groups_by_id
    hdb = MagicMock()
    hdb.get_by_attr.return_value = make_hunt()
    pdb = MagicMock()
    # Members of the group: the two that move plus a shared feeder that stays.
    pdb.get_all_from_tag_group.return_value = [
        _puzzle(50, "Spies (meta)"), _puzzle(51, "Mole"), _puzzle(52, "Double agent")]
    audit = AsyncMock()

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", audit),
        patch("bot.store.TagGroupJsonDb", rdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.web.actions.puzzles_lock", _noop_lock),
        patch("bot.core.category_layout.primary_category", MagicMock(return_value=None)),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), cog=cog, audit=audit)
    finally:
        stop_patches(patches)


# --- preview ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_lists_moving_and_staying_puzzles_and_free_slots(web):
    resp = await web.client.get("/api/tag_groups/5/extract-category/preview")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body == {
        "own_category": False,
        "free_category_slots": 3,
        "moving": [{"id": 50, "name": "Spies (meta)"}, {"id": 51, "name": "Mole"}],
        "staying": [{"id": 52, "name": "Double agent"}],
    }


@pytest.mark.asyncio
async def test_preview_refuses_a_group_without_a_target(web):
    resp = await web.client.get("/api/tag_groups/8/extract-category/preview")
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "not_a_meta"


# --- extract ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_calls_the_shared_service_seam(web):
    resp = await web.client.post("/api/tag_groups/5/extract-category")
    assert resp.status_code == 200
    assert (await resp.get_json()) == {"ok": True, "category_name": "Spies"}
    web.cog.service.extract_target_to_category.assert_awaited_once()
    assert web.cog.service.extract_target_to_category.await_args.args[2] is META
    web.audit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["already_own_category", "no_category_space",
                                  "no_template_category"])
async def test_extract_refusals_come_back_as_slugs(web, slug):
    web.cog.service.extract_target_to_category.side_effect = RuntimeError(slug)
    resp = await web.client.post("/api/tag_groups/5/extract-category")
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == slug
    web.audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_needs_a_live_guild():
    cog = MagicMock()
    cog.service.extract_target_to_category = AsyncMock()
    app = make_web_app("actions", cog=cog)
    getattr(app, "bot").get_guild.return_value = None
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_tag_group",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.store.HuntJsonDb", MagicMock(get_by_attr=MagicMock(return_value=make_hunt()))),
    ]
    start_patches(patches)
    try:
        resp = await app.test_client().post("/api/tag_groups/5/extract-category")
    finally:
        stop_patches(patches)
    assert resp.status_code == 503
    cog.service.extract_target_to_category.assert_not_awaited()


# --- fold (consolidate) ----------------------------------------------------

@pytest.mark.asyncio
async def test_fold_passes_meta_destination_and_no_force(web):
    resp = await web.client.post("/api/tag_groups/5/consolidate",
                                 json={"destination_tag_group_id": 6})
    assert resp.status_code == 200
    assert (await resp.get_json()) == {"ok": True, "destination_name": "Round One"}
    call = web.cog.service.consolidate_to.await_args
    assert call.args[2] is META and call.args[3] is DEST
    assert call.kwargs["force"] is False


@pytest.mark.asyncio
async def test_fold_would_overflow_is_409_so_the_modal_can_ask_again(web):
    web.cog.service.consolidate_to.side_effect = RuntimeError("would_overflow")
    resp = await web.client.post("/api/tag_groups/5/consolidate",
                                 json={"destination_tag_group_id": 6})
    assert resp.status_code == 409
    assert (await resp.get_json())["error"] == "would_overflow"
    web.audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_fold_with_force_passes_force_through(web):
    resp = await web.client.post("/api/tag_groups/5/consolidate",
                                 json={"destination_tag_group_id": 6, "force": True})
    assert resp.status_code == 200
    assert web.cog.service.consolidate_to.await_args.kwargs["force"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("dest_id,slug", [(5, "same_group"), (7, "bad_destination"),
                                          (404, "bad_destination"), (None, "bad_destination")])
async def test_fold_rejects_bad_destinations(web, dest_id, slug):
    resp = await web.client.post("/api/tag_groups/5/consolidate",
                                 json={"destination_tag_group_id": dest_id})
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == slug
    web.cog.service.consolidate_to.assert_not_awaited()

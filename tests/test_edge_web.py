"""Milestone C — Phase 1 failing tests for the web API edge/type parity.

Covers the backward-compatible extensions to the dashboard write-actions:

  POST /api/hunts/<uid>/metas      -> create_tag_group(..., round_type=<type>)
  POST /api/puzzles/<id>/meta      -> tag_with_edge / tag_edge_only (edge + only)
  POST /api/puzzles/<id>/convert-to-capstone  (and friends) -> convert_to_type

We build a minimal Quart app registering only ``actions_bp`` (no CSRF guard), patch
the auth/visibility helpers so the request reaches the view body, and assert which
service method the view dispatches to. The session proxy in ``bot.web.auth`` is
patched to a plain dict so ``login_required`` passes without cookie machinery.

Phase 1 expectation: these fail because the views still call the OLD service methods
(or the new routes are unregistered). Phase 4 wires them up. The fixture is local to
this file (the parallel runner shards by file).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle, make_round


# Service methods the views may await — every one must be an AsyncMock so the view
# body runs to completion regardless of which one the current code path calls.
_ASYNC_SERVICE_METHODS = [
    "tag_with_edge", "tag_edge_only", "untag_from",
    "create_tag_group", "convert_to_type", "convert_to_meta",
    "convert_to_puzzle", "refresh_unassigned_safe", "set_source_mode",
]


def _build_cog():
    cog = MagicMock()
    svc = cog.service
    for name in _ASYNC_SERVICE_METHODS:
        setattr(svc, name, AsyncMock())
    # create_tag_group returns (TagGroupData, PuzzleData); channel_id=0 so the
    # create_meta view skips the Discord welcome path.
    svc.create_tag_group = AsyncMock(return_value=(make_round(id=5), make_puzzle(id=9, channel_id=0)))
    svc.convert_to_type = AsyncMock(return_value=(make_round(id=5), "flipped 3 feeds → informs"))
    return cog


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
    cog = _build_cog()
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    ctx = {"hunt": {"id": 10, "uid": "U1", "archived": False}, "guild_snowflake": "123"}

    target_group = make_round(id=7, group_code="X", category_id=999, target_puzzle_id=9)
    target_group.hunt_id = 10
    target_group.type = "Metapuzzle"
    puzzle = make_puzzle(id=5, channel_id=222, is_target=True)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._visible_hunt_by_uid", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._organiser_hunt", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions.require_organiser", AsyncMock(return_value=None)),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.HuntJsonDb"),
        patch("bot.store.PuzzleJsonDb"),
        patch("bot.store.TagGroupJsonDb"),
    ]
    started = [p.start() for p in patches]
    _hdb, _pdb, _rdb = started[6], started[7], started[8]
    _pdb.get_by_attr.return_value = puzzle
    _pdb.check_duplicates_in_hunt.return_value = False
    _rdb.get_by_attr.return_value = target_group
    _hdb.get_by_attr.return_value = MagicMock()

    try:
        yield SimpleNamespace(
            app=app, client=app.test_client(), cog=cog, svc=cog.service,
            puzzle=puzzle, target_group=target_group,
            HuntJsonDb=_hdb, PuzzleJsonDb=_pdb, TagGroupJsonDb=_rdb,
        )
    finally:
        for p in patches:
            p.stop()


def _edge_of(call):
    """Extract the edge argument from a tag_with_edge/tag_edge_only(puzzle, group, edge, ...) call."""
    if "edge" in call.kwargs:
        return call.kwargs["edge"]
    return call.args[2]


# ---------------------------------------------------------------------------
# Create endpoint — type param
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_meta_default_type(web):
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo"})
    assert resp.status_code == 200
    web.svc.create_tag_group.assert_called_once()
    assert web.svc.create_tag_group.call_args.kwargs.get("round_type") == "Metapuzzle"


@pytest.mark.asyncio
async def test_create_meta_capstone_type(web):
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Capstone"})
    assert resp.status_code == 200
    web.svc.create_tag_group.assert_called_once()
    assert web.svc.create_tag_group.call_args.kwargs.get("round_type") == "Capstone"


@pytest.mark.asyncio
async def test_create_meta_round_alias_stores_metapuzzle_with_suffix(web):
    # "Round" is a create-time alias: stored as Metapuzzle, meta named "<name> - meta".
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Round"})
    assert resp.status_code == 200
    kwargs = web.svc.create_tag_group.call_args.kwargs
    assert kwargs.get("round_type") == "Metapuzzle"
    assert kwargs.get("meta_puzzle_name") == "Foo - meta"


@pytest.mark.asyncio
async def test_create_meta_non_round_keeps_group_name(web):
    # Non-Round types pass the bare group name (no suffix).
    await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Metapuzzle"})
    assert web.svc.create_tag_group.call_args.kwargs.get("meta_puzzle_name") == "Foo"


@pytest.mark.asyncio
async def test_create_meta_runaround_sets_subtree_source_mode(web):
    # Runaround groups aggregate over their subtree, mirroring the bang path.
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Runaround"})
    assert resp.status_code == 200
    web.svc.set_source_mode.assert_awaited_once()
    args = web.svc.set_source_mode.call_args.args
    assert args[2] == "subtree"
    assert args[1] is web.svc.create_tag_group.return_value[0]   # the new round


# ---------------------------------------------------------------------------
# Phase 1 (1C): Group type + service-owned welcomes. These FAIL until Phase 2
# wires the route to pass meta_puzzle_name=False for Group and a real notifier
# for every type (the manual welcome block is deleted).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_meta_group_makes_no_meta(web):
    web.svc.create_tag_group = AsyncMock(return_value=(make_round(id=5), None))
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Group"})
    assert resp.status_code == 200
    kwargs = web.svc.create_tag_group.call_args.kwargs
    assert kwargs.get("meta_puzzle_name") is False          # no target puzzle
    assert kwargs.get("round_type") == "Group"
    assert kwargs.get("notifier") is not None               # service posts the welcome


@pytest.mark.asyncio
async def test_create_meta_passes_notifier_for_all_types(web):
    # The web route no longer posts welcomes manually — it hands a real notifier to
    # the service for EVERY type. (Was notifier=None previously.)
    await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Metapuzzle"})
    assert web.svc.create_tag_group.call_args.kwargs.get("notifier") is not None


# ---------------------------------------------------------------------------
# Membership endpoint — edge + only params
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retag_default_edge_feeds(web):
    await web.client.post("/api/puzzles/5/meta", json={"group_code": "X", "mode": "add"})
    web.svc.tag_with_edge.assert_called_once()
    assert _edge_of(web.svc.tag_with_edge.call_args) == "feeds"


@pytest.mark.asyncio
async def test_retag_informs_edge(web):
    await web.client.post(
        "/api/puzzles/5/meta", json={"group_code": "X", "mode": "add", "edge": "informs"}
    )
    web.svc.tag_with_edge.assert_called_once()
    assert _edge_of(web.svc.tag_with_edge.call_args) == "informs"


@pytest.mark.asyncio
async def test_retag_add_only_true(web):
    await web.client.post(
        "/api/puzzles/5/meta", json={"group_code": "X", "mode": "add", "only": True}
    )
    web.svc.tag_edge_only.assert_called_once()


@pytest.mark.asyncio
async def test_retag_mode_move_compat(web):
    # Move into a fed meta defaults to a feeds edge.
    await web.client.post("/api/puzzles/5/meta", json={"group_code": "X", "mode": "move"})
    web.svc.tag_edge_only.assert_called_once()
    assert _edge_of(web.svc.tag_edge_only.call_args) == "feeds"


@pytest.mark.asyncio
async def test_retag_bare_group_defaults_none(web):
    # A bare group (no target puzzle) pools the puzzle with a 'none' edge.
    bare = make_round(id=8, group_code="B", category_id=1)  # target_puzzle_id=0
    bare.hunt_id = 10
    web.TagGroupJsonDb.get_by_attr.return_value = bare
    await web.client.post("/api/puzzles/5/meta", json={"group_code": "B", "mode": "move"})
    web.svc.tag_edge_only.assert_called_once()
    assert _edge_of(web.svc.tag_edge_only.call_args) == "none"


@pytest.mark.asyncio
async def test_retag_capstone_defaults_informs(web):
    # Moving into a capstone informs (no feeds).
    cap = make_round(id=8, group_code="C", category_id=1, target_puzzle_id=99)
    cap.hunt_id = 10
    cap.type = "Capstone"
    web.TagGroupJsonDb.get_by_attr.return_value = cap
    await web.client.post("/api/puzzles/5/meta", json={"group_code": "C", "mode": "move"})
    web.svc.tag_edge_only.assert_called_once()
    assert _edge_of(web.svc.tag_edge_only.call_args) == "informs"


# ---------------------------------------------------------------------------
# Convert endpoints — new routes + summary field
# ---------------------------------------------------------------------------

def test_convert_to_capstone_endpoint_exists(web):
    rules = {r.rule for r in web.app.url_map.iter_rules()}
    assert "/api/puzzles/<int:puzzle_id>/convert-to-capstone" in rules


@pytest.mark.asyncio
async def test_convert_to_capstone_returns_summary(web):
    resp = await web.client.post("/api/puzzles/5/convert-to-capstone", json={})
    data = await resp.get_json(silent=True) or {}
    assert "summary" in data

"""Every change to a puzzle's images or its source-evidence images refreshes
that puzzle's pinned info message (its Images list), after the DB write."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from tests.conftest import (make_api_hunt, make_cog, make_ctx, make_hunt, make_hunt_ctx,
                            make_puzzle, make_web_app, start_patches, stop_patches)


def _image(id=1, puzzle_id=20):
    return SimpleNamespace(id=id, puzzle_id=puzzle_id, media_id=7,
                           source_region_id=None, position=0, created_at=None)


def _refreshed(cog):
    return [c.args[2].id for c in cog.web_refresh_info.await_args_list]


@pytest.fixture
def img():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()
    pdb, mdb, hdb, pidb = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    pdb.get_by_attr.side_effect = lambda id: make_puzzle(id=id, hunt_id=10)
    mdb.get_by_id.return_value = SimpleNamespace(id=7, hunt_id=10)
    hdb.get_by_attr.return_value = make_hunt(id=10)
    pidb.get_by_puzzle.return_value = [_image(1), _image(2)]
    pidb.get_by_id.return_value = _image()
    pidb.create.return_value = _image()
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_puzzle", AsyncMock(return_value=(ctx, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.core.puzzle_images.PuzzleImageJsonDb", pidb),
        patch("bot.web.actions.events.publish"),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), cog=cog, pidb=pidb)
    finally:
        stop_patches(patches)


async def test_attach_refreshes_the_puzzle_pin(img):
    resp = await img.client.post("/api/puzzles/20/images", json={"media_id": 7})
    assert resp.status_code == 201
    assert _refreshed(img.cog) == [20]


async def test_reorder_refreshes_the_puzzle_pin(img):
    resp = await img.client.patch("/api/puzzles/20/images", json={"ordered_ids": [2, 1]})
    assert resp.status_code == 200, await resp.get_data()
    assert _refreshed(img.cog) == [20]


async def test_detach_refreshes_the_puzzle_pin(img):
    resp = await img.client.delete("/api/puzzles/20/images/1")
    assert resp.status_code == 200
    assert _refreshed(img.cog) == [20]


async def test_a_failing_refresh_does_not_fail_the_request(img):
    img.cog.web_refresh_info.side_effect = RuntimeError("discord down")
    resp = await img.client.delete("/api/puzzles/20/images/1")
    assert resp.status_code == 200


async def test_no_refresh_when_the_write_is_refused(img):
    from bot.core import puzzle_images
    with patch("bot.core.puzzle_images.detach", side_effect=puzzle_images.ImageNotFound("x")):
        resp = await img.client.delete("/api/puzzles/20/images/1")
    assert resp.status_code == 404
    img.cog.web_refresh_info.assert_not_awaited()


# --- evidence routes (actions) ------------------------------------------------

@pytest.fixture
def ev():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    for name in ("link_informs_for_evidence_safe", "unlink_informs_for_evidence_safe",
                 "refresh_target_for_puzzle", "refresh_evidence_safe"):
        setattr(cog.service, name, AsyncMock())
    app = make_web_app("actions", cog=cog)
    item = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1, target_puzzle_id=None,
                           note="old", media_id=7)
    edb, hdb, pdb, mdb = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    edb.get_by_id.return_value = item
    edb.update_note.return_value = item
    edb.set_target.return_value = item
    edb.update_media.return_value = item
    hdb.get_by_attr.return_value = SimpleNamespace(id=10, uid="U1", archived=False)
    pdb.get_by_attr.side_effect = lambda id: make_puzzle(id=id, hunt_id=10)
    mdb.get_by_id.return_value = SimpleNamespace(id=8, hunt_id=10)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_evidence",
              AsyncMock(return_value=(make_hunt_ctx(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.EvidenceJsonDb", edb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.MediaJsonDb", mdb),
        patch("bot.web.actions.events.publish"),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), cog=cog, item=item, edb=edb)
    finally:
        stop_patches(patches)


async def test_deleting_image_evidence_refreshes_its_source_puzzle(ev):
    resp = await ev.client.delete("/api/evidence/5")
    assert resp.status_code == 200
    assert _refreshed(ev.cog) == [1]


async def test_deleting_evidence_without_media_does_not_refresh(ev):
    ev.item.media_id = None
    resp = await ev.client.delete("/api/evidence/5")
    assert resp.status_code == 200
    ev.cog.web_refresh_info.assert_not_awaited()


async def test_deleting_evidence_without_a_source_puzzle_does_not_refresh(ev):
    ev.item.source_puzzle_id = None
    resp = await ev.client.delete("/api/evidence/5")
    assert resp.status_code == 200
    ev.cog.web_refresh_info.assert_not_awaited()


async def test_swapping_the_image_refreshes_the_source_puzzle(ev):
    resp = await ev.client.patch("/api/evidence/5", json={"media_id": 8})
    assert resp.status_code == 200
    assert _refreshed(ev.cog) == [1]


async def test_editing_the_note_of_image_evidence_refreshes_the_source_puzzle(ev):
    """The pin's link label is the evidence note."""
    resp = await ev.client.patch("/api/evidence/5", json={"note": "new"})
    assert resp.status_code == 200
    assert _refreshed(ev.cog) == [1]


async def test_a_target_only_change_does_not_refresh_pins(ev):
    resp = await ev.client.patch("/api/evidence/5", json={"target_puzzle_id": 9})
    assert resp.status_code == 200
    ev.cog.web_refresh_info.assert_not_awaited()


async def test_a_note_edit_on_evidence_without_media_does_not_refresh(ev):
    ev.item.media_id = None
    resp = await ev.client.patch("/api/evidence/5", json={"note": "new"})
    assert resp.status_code == 200
    ev.cog.web_refresh_info.assert_not_awaited()


# --- POST /api/evidence ---------------------------------------------------------

@pytest.fixture
def post_ev():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    cog.service.refresh_evidence_safe = AsyncMock()
    cog.service.refresh_informs_targets = AsyncMock()
    cog.service.log_clues = AsyncMock(
        side_effect=lambda hunt, puzzle, notes, media_id=None, source_sheet_id=None: [
            SimpleNamespace(id=1, note=n) for n in notes])
    app = make_web_app("api", cog=cog)
    pdb, hdb = MagicMock(), MagicMock()
    pdb.get_by_attr.side_effect = lambda id: make_puzzle(id=id, hunt_id=10)
    hdb.get_by_attr.return_value = SimpleNamespace(id=10)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt",
              AsyncMock(return_value=(make_api_hunt(), {"guild_snowflake": "123"}))),
        patch("bot.web.actions._require_hunt_media", MagicMock(return_value=(MagicMock(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), cog=cog)
    finally:
        stop_patches(patches)


async def test_logging_image_evidence_from_a_puzzle_refreshes_its_pin(post_ev):
    resp = await post_ev.client.post("/api/evidence", json={
        "hunt_uid": "U1", "source_puzzle_id": 9, "media_id": 7, "notes": ["n"]})
    assert resp.status_code == 201
    assert _refreshed(post_ev.cog) == [9]


async def test_logging_text_only_evidence_does_not_refresh_a_pin(post_ev):
    resp = await post_ev.client.post("/api/evidence", json={
        "hunt_uid": "U1", "source_puzzle_id": 9, "notes": ["n"]})
    assert resp.status_code == 201
    post_ev.cog.web_refresh_info.assert_not_awaited()


async def test_logging_image_evidence_with_no_source_puzzle_does_not_refresh(post_ev):
    resp = await post_ev.client.post("/api/evidence", json={
        "hunt_uid": "U1", "media_id": 7, "notes": ["n"]})
    assert resp.status_code == 201
    post_ev.cog.web_refresh_info.assert_not_awaited()


# --- canvas regions ----------------------------------------------------------------

@pytest.fixture
def reg():
    cog = MagicMock()
    cog.web_refresh_info = AsyncMock()
    app = make_web_app("actions", cog=cog)
    ctx = make_hunt_ctx()
    existing = SimpleNamespace(id=3, canvas_id=4, puzzle_id=20, role="locates",
                               shape='{"type":"rect"}', label="", note=None)
    rdb, pdb, hdb = MagicMock(), MagicMock(), MagicMock()
    rdb.get_by_id.return_value = existing
    pdb.get_by_attr.side_effect = lambda id: make_puzzle(id=id, hunt_id=10)
    hdb.get_by_attr.return_value = make_hunt(id=10)
    canvas_row = SimpleNamespace(id=4, media_id=1)
    cs = MagicMock()
    cs.LOCATES, cs.INFORMS = "locates", "informs"
    cs.RegionRejected = type("RegionRejected", (Exception,), {})
    cs.create_region.return_value = SimpleNamespace(id=3)
    cs.update_region.return_value = SimpleNamespace(id=3)
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._canvas_ctx", AsyncMock(return_value=(ctx, canvas_row, None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.web.actions._require_hunt_puzzle", MagicMock(return_value=(None, None))),
        patch("bot.web.actions._crop_region_media", AsyncMock(return_value=55)),
        patch("bot.web.actions._region_crop_media_id", AsyncMock(return_value=55)),
        patch("bot.web.actions.web_data.region_to_json", MagicMock(return_value={})),
        patch("bot.core.canvas.create_region", cs.create_region),
        patch("bot.core.canvas.update_region", cs.update_region),
        patch("bot.core.canvas.delete_region", cs.delete_region),
        patch("bot.store.CanvasRegionJsonDb", rdb),
        patch("bot.store.PuzzleJsonDb", pdb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.web.actions.events.publish"),
    ]
    start_patches(patches)
    try:
        yield SimpleNamespace(client=app.test_client(), cog=cog, existing=existing)
    finally:
        stop_patches(patches)


_SHAPE = {"type": "rect", "x": 0, "y": 0, "w": 1, "h": 1}


async def test_creating_a_locates_region_refreshes_the_puzzle_pin(reg):
    resp = await reg.client.post("/api/canvases/4/regions", json={
        "shape": _SHAPE, "role": "locates", "puzzle_id": 21})
    assert resp.status_code == 201, await resp.get_data()
    assert _refreshed(reg.cog) == [21]


async def test_creating_an_informs_region_does_not_refresh_a_pin(reg):
    resp = await reg.client.post("/api/canvases/4/regions", json={
        "shape": _SHAPE, "role": "informs", "puzzle_id": 21})
    assert resp.status_code == 201
    reg.cog.web_refresh_info.assert_not_awaited()


async def test_relinking_a_locates_region_refreshes_old_and_new_puzzles(reg):
    resp = await reg.client.patch("/api/canvases/4/regions/3", json={"puzzle_id": 21})
    assert resp.status_code == 200, await resp.get_data()
    assert sorted(_refreshed(reg.cog)) == [20, 21]


async def test_a_label_edit_does_not_refresh_pins(reg):
    resp = await reg.client.patch("/api/canvases/4/regions/3", json={"label": "x"})
    assert resp.status_code == 200
    reg.cog.web_refresh_info.assert_not_awaited()


async def test_deleting_a_locates_region_refreshes_its_puzzle(reg):
    resp = await reg.client.delete("/api/canvases/4/regions/3")
    assert resp.status_code == 200
    assert _refreshed(reg.cog) == [20]


async def test_deleting_an_informs_region_does_not_refresh(reg):
    reg.existing.role = "informs"
    resp = await reg.client.delete("/api/canvases/4/regions/3")
    assert resp.status_code == 200
    reg.cog.web_refresh_info.assert_not_awaited()


# --- !clue ---------------------------------------------------------------------------

async def test_clue_with_an_attachment_refreshes_the_pin():
    puzzle = make_puzzle(id=5, hunt_id=10)
    hunt = make_hunt()
    cog = make_cog(puzzle, hunt)
    ctx = make_ctx()
    cog._store_clue_attachment = AsyncMock(return_value=(7, None))
    cog.service.log_clues = AsyncMock(return_value=[MagicMock()])
    cog.service.refresh_evidence_safe = AsyncMock()
    cog.web_refresh_info = AsyncMock()

    await Puzzles.clue.callback(cog, ctx, note="see picture")

    cog.web_refresh_info.assert_awaited_once_with(ctx.guild, hunt, puzzle)


async def test_clue_without_an_attachment_does_not_refresh_the_pin():
    puzzle = make_puzzle(id=5, hunt_id=10)
    cog = make_cog(puzzle, make_hunt())
    ctx = make_ctx()
    cog._store_clue_attachment = AsyncMock(return_value=(None, None))
    cog.service.log_clues = AsyncMock(return_value=[MagicMock()])
    cog.service.refresh_evidence_safe = AsyncMock()
    cog.web_refresh_info = AsyncMock()

    await Puzzles.clue.callback(cog, ctx, note="text only")

    cog.web_refresh_info.assert_not_awaited()


# --- canvas delete ---------------------------------------------------------------------

async def test_deleting_a_canvas_refreshes_pins_of_puzzles_that_lose_images(reg):
    """The cascade drops region-sourced puzzle_images rows; the ids are read
    BEFORE the delete and refreshed AFTER it."""
    calls = []
    pidb = MagicMock()
    pidb.puzzle_ids_for_canvas.side_effect = lambda cid: calls.append(("ids", cid)) or [20, 21]
    cdb = MagicMock()
    cdb.delete.side_effect = lambda cid: calls.append(("delete", cid))
    mdb = MagicMock()
    mdb.get_by_id.return_value = None
    with patch("bot.store.PuzzleImageJsonDb", pidb), patch("bot.store.CanvasJsonDb", cdb), \
         patch("bot.store.MediaJsonDb", mdb):
        resp = await reg.client.delete("/api/canvases/4")
    assert resp.status_code == 200, await resp.get_data()
    assert calls == [("ids", 4), ("delete", 4)]
    assert sorted(_refreshed(reg.cog)) == [20, 21]


async def test_deleting_a_canvas_with_no_region_images_refreshes_nothing(reg):
    pidb = MagicMock()
    pidb.puzzle_ids_for_canvas.return_value = []
    mdb = MagicMock()
    mdb.get_by_id.return_value = None
    with patch("bot.store.PuzzleImageJsonDb", pidb), patch("bot.store.CanvasJsonDb", MagicMock()), \
         patch("bot.store.MediaJsonDb", mdb):
        resp = await reg.client.delete("/api/canvases/4")
    assert resp.status_code == 200
    reg.cog.web_refresh_info.assert_not_awaited()

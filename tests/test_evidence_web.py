"""Web-route security/correctness tests for the evidence PATCH/DELETE endpoints.

Mirrors the harness in test_edge_web.py: a minimal Quart app registering only
``actions_bp``, with auth/visibility helpers patched so the request body runs.

Covers correctness-review findings:
  F1 — evidence PATCH/DELETE must be hunt-visibility checked, must reject
       archived hunts (PATCH had no guard), and must reject a target_puzzle_id
       that belongs to a different hunt.
  F3 — evidence note-edit/target-change/delete must refresh the right target
       contribution tables and run the informs cleanup on delete.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_puzzle


def _ctx(archived=False):
    return {"hunt": {"id": 10, "uid": "U1", "archived": archived}, "guild_snowflake": "123"}


@pytest.fixture
def web():
    from quart import Quart
    from bot.web.actions import actions_bp

    app = Quart(__name__)
    app.secret_key = "test-secret-key-must-be-long-enough-0123456789"

    cog = MagicMock()
    svc = cog.service
    for name in (
        "link_informs_for_evidence_safe", "unlink_informs_for_evidence_safe",
        "refresh_target_for_puzzle", "refresh_evidence_safe",
    ):
        setattr(svc, name, AsyncMock())
    app.bot = MagicMock()
    app.bot.get_cog.return_value = cog
    app.bot.get_guild.return_value = MagicMock()
    app.register_blueprint(actions_bp)

    # Evidence item id=5 lives in hunt 10, sourced from puzzle 1.
    item = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1, target_puzzle_id=None,
                           note="old", media_id=None)

    edb = MagicMock()
    edb.get_by_id.return_value = item
    edb.update_note.return_value = item
    edb.set_target.return_value = item

    hdb = MagicMock()
    hunt_obj = SimpleNamespace(id=10, uid="U1", archived=False)
    hdb.get_by_attr.return_value = hunt_obj

    pdb = MagicMock()
    # A puzzle in the SAME hunt (10); tests override for the cross-hunt case.
    pdb.get_by_attr.return_value = make_puzzle(id=9, hunt_id=10)

    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions._visible_hunt_for_evidence", AsyncMock(return_value=(_ctx(), None))),
        patch("bot.web.actions._audit", AsyncMock()),
        patch("bot.store.EvidenceJsonDb", edb),
        patch("bot.store.HuntJsonDb", hdb),
        patch("bot.store.PuzzleJsonDb", pdb),
    ]
    started = [p.start() for p in patches]
    try:
        yield SimpleNamespace(
            app=app, client=app.test_client(), cog=cog, svc=svc,
            item=item, hunt_obj=hunt_obj, edb=edb, hdb=hdb, pdb=pdb,
            vis=started[1],
        )
    finally:
        for p in patches:
            p.stop()


# --- F1: visibility gate -----------------------------------------------------

@pytest.mark.asyncio
async def test_patch_forbidden_when_hunt_not_visible(web):
    web.vis.return_value = (None, ({"error": "forbidden"}, 403))

    resp = await web.client.patch("/api/evidence/5", json={"note": "hax"})

    assert resp.status_code == 403
    web.edb.update_note.assert_not_called()
    web.edb.set_target.assert_not_called()


@pytest.mark.asyncio
async def test_delete_forbidden_when_hunt_not_visible(web):
    web.vis.return_value = (None, ({"error": "forbidden"}, 403))

    resp = await web.client.delete("/api/evidence/5")

    assert resp.status_code == 403
    web.edb.delete.assert_not_called()


# --- F1: archived guard (PATCH lacked it) ------------------------------------

@pytest.mark.asyncio
async def test_patch_rejects_archived_hunt(web):
    web.vis.return_value = (_ctx(archived=True), None)

    resp = await web.client.patch("/api/evidence/5", json={"note": "edit"})

    assert resp.status_code == 403
    assert (await resp.get_json())["error"] == "hunt_archived"
    web.edb.update_note.assert_not_called()


@pytest.mark.asyncio
async def test_delete_rejects_archived_hunt(web):
    web.vis.return_value = (_ctx(archived=True), None)

    resp = await web.client.delete("/api/evidence/5")

    assert resp.status_code == 403
    web.edb.delete.assert_not_called()


# --- F1: cross-hunt target routing -------------------------------------------

@pytest.mark.asyncio
async def test_patch_rejects_target_in_other_hunt(web):
    # Target puzzle 99 belongs to a DIFFERENT hunt (77).
    web.pdb.get_by_attr.return_value = make_puzzle(id=99, hunt_id=77)

    resp = await web.client.patch("/api/evidence/5", json={"target_puzzle_id": 99})

    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "cross_hunt_target"
    web.edb.set_target.assert_not_called()


# --- F3: target contribution rollups stay fresh ------------------------------

@pytest.mark.asyncio
async def test_note_edit_refreshes_current_target(web):
    """A note-only edit must refresh the current target's contribution table so
    the new note text shows there (previously only the Evidence tab refreshed)."""
    web.item.target_puzzle_id = 42

    resp = await web.client.patch("/api/evidence/5", json={"note": "fresh note"})

    assert resp.status_code == 200
    web.svc.refresh_target_for_puzzle.assert_any_call(web.hunt_obj, 42)


@pytest.mark.asyncio
async def test_target_change_refreshes_old_and_new(web):
    """Moving evidence to a new target must refresh BOTH the old target (loses
    the row) and the new target (gains it)."""
    web.item.target_puzzle_id = 11  # old target
    moved = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1, target_puzzle_id=22,
                            note="old", media_id=None)
    web.edb.set_target.return_value = moved

    resp = await web.client.patch("/api/evidence/5", json={"target_puzzle_id": 22})

    assert resp.status_code == 200
    refreshed = {c.args[1] for c in web.svc.refresh_target_for_puzzle.call_args_list}
    assert {11, 22} <= refreshed


@pytest.mark.asyncio
async def test_delete_refreshes_old_target_and_unlinks_informs(web):
    """Deleting routed evidence must refresh the old target and run the same
    informs cleanup as reassignment (drop auto-added informs when last item gone)."""
    web.item.target_puzzle_id = 33

    resp = await web.client.delete("/api/evidence/5")

    assert resp.status_code == 200
    web.svc.refresh_target_for_puzzle.assert_any_call(web.hunt_obj, 33)
    web.svc.unlink_informs_for_evidence_safe.assert_any_call(web.hunt_obj, 1, 33)


# --- get_evidence: null source, media fields ---------------------------------

class TestGetEvidenceNullSource:
    def test_null_source_serialises_as_null_name_alongside_a_real_source(self):
        """Two items, not one: a fix that returns None for EVERY row's
        source_puzzle_name (e.g. dropping the fallback lookup entirely) would
        still pass a single sourceless-item test. Pinning a resolved name
        alongside the null case rules that out, and also proves None never
        reaches resolve_puzzle_names (asserted via call_args, not just by
        patching it to return {})."""
        from unittest.mock import patch
        from types import SimpleNamespace
        from bot.web import data

        sourced = SimpleNamespace(id=5, source_puzzle_id=1, source_sheet_id=None,
                                  target_puzzle_id=2,
                                  note="a clue", media_id=None, created_at=None)
        sourceless = SimpleNamespace(id=9, source_puzzle_id=None, source_sheet_id=None,
                                     target_puzzle_id=None,
                                     note="a fact", media_id=None, created_at=None)
        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names") as resolve:
            edb.get_all.return_value = [sourced, sourceless]
            resolve.return_value = {1: "Feeder One", 2: "Meta Two"}
            out = data.get_evidence(10)

        by_id = {o["id"]: o for o in out}
        assert by_id[5]["source_puzzle_name"] == "Feeder One"  # real source still resolves
        assert by_id[9]["source_puzzle_id"] is None
        assert by_id[9]["source_puzzle_name"] is None
        assert by_id[9]["media_id"] is None
        assert by_id[9]["media_url"] is None

        # None must never reach the batched name-lookup query.
        queried_ids = resolve.call_args.args[0]
        assert None not in queried_ids

    def test_media_id_becomes_a_url(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from bot.web import data

        item = SimpleNamespace(id=9, source_puzzle_id=None, source_sheet_id=None,
                               target_puzzle_id=None,
                               note="", media_id=7, created_at=None)
        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}):
            edb.get_all.return_value = [item]
            out = data.get_evidence(10)

        assert out[0]["media_id"] == 7
        assert out[0]["media_url"] == "/api/media/7"


# --- get_evidence: canvas-region source --------------------------------------

class TestGetEvidenceRegionSource:
    def _item(self, **kw):
        from types import SimpleNamespace
        base = dict(id=1, source_puzzle_id=None, source_sheet_id=None,
                    source_region_id=None, target_puzzle_id=None,
                    note="", media_id=None, created_at=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_a_region_source_resolves_to_its_name(self):
        """Two region-sourced items with DIFFERENT names, not one: serialising
        every row with the first (or any single) resolved name would satisfy a
        one-item test while mapping the whole board to one label."""
        from unittest.mock import patch
        from bot.web import data

        a = self._item(id=5, source_region_id=3)
        b = self._item(id=6, source_region_id=4)
        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}), \
             patch("bot.core.puzzle_service.resolve_region_names") as regions:
            edb.get_all.return_value = [a, b]
            regions.return_value = {3: "Hex maze", 4: "Canvas (sheet.png)"}
            out = data.get_evidence(10)

        by_id = {o["id"]: o for o in out}
        assert by_id[5]["source_region_id"] == 3
        assert by_id[5]["source_region_name"] == "Hex maze"
        assert by_id[6]["source_region_id"] == 4
        assert by_id[6]["source_region_name"] == "Canvas (sheet.png)"

    def test_a_non_region_item_carries_a_null_region_and_is_not_looked_up(self):
        from unittest.mock import patch
        from bot.web import data

        item = self._item(id=9, source_puzzle_id=1)
        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={1: "P"}), \
             patch("bot.core.puzzle_service.resolve_region_names") as regions:
            edb.get_all.return_value = [item]
            regions.return_value = {}
            out = data.get_evidence(10)

        assert out[0]["source_region_id"] is None
        assert out[0]["source_region_name"] is None
        # Unlike the puzzle ids, region ids are handed over unfiltered — the
        # resolver drops the Nones, exactly as it does for sheets. That contract
        # is pinned on resolve_region_names itself, below.
        assert regions.called

    def test_an_unresolvable_region_still_names_itself(self):
        """The region row vanished between the two queries. A blank Source cell
        would read as 'no source', which is a different and wrong claim."""
        from unittest.mock import patch
        from bot.web import data

        with patch("bot.store.EvidenceJsonDb") as edb, \
             patch("bot.core.puzzle_service.resolve_puzzle_names", return_value={}), \
             patch("bot.core.puzzle_service.resolve_region_names", return_value={}):
            edb.get_all.return_value = [self._item(id=9, source_region_id=12)]
            out = data.get_evidence(10)

        assert out[0]["source_region_name"] == "Region 12"


class TestResolveRegionNames:
    def test_none_and_zero_never_reach_the_query(self):
        from unittest.mock import patch
        from bot.core import puzzle_service

        with patch("bot.core.puzzle_service.CanvasRegionJsonDb") as db:
            db.get_names_by_ids.return_value = {3: "Hex maze"}
            out = puzzle_service.resolve_region_names([3, None, 0, 3])

        assert out == {3: "Hex maze"}
        assert db.get_names_by_ids.call_args.args[0] == {3}

    def test_an_all_empty_batch_skips_the_query_entirely(self):
        from unittest.mock import patch
        from bot.core import puzzle_service

        with patch("bot.core.puzzle_service.CanvasRegionJsonDb") as db:
            out = puzzle_service.resolve_region_names([None, None])

        assert out == {}
        db.get_names_by_ids.assert_not_called()


# --- replacing an evidence item's image in place -----------------------------

@pytest.mark.asyncio
async def test_patch_replaces_the_media_and_keeps_the_old_row(web):
    """Swapping the image points the evidence row at the new media. The OLD
    media row is deliberately untouched — nothing on the evidence path garbage-
    collects media (deleting an item leaves its file too), and a replacement
    must never destroy the only copy of an image."""
    swapped = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1,
                              target_puzzle_id=None, note="old", media_id=77)
    web.edb.update_media.return_value = swapped
    media = SimpleNamespace(id=77, hunt_id=10)

    with patch("bot.store.MediaJsonDb") as mdb:
        mdb.get_by_id.return_value = media
        resp = await web.client.patch("/api/evidence/5", json={"media_id": 77})

    assert resp.status_code == 200
    assert (await resp.get_json())["media_id"] == 77
    web.edb.update_media.assert_called_once_with(5, 77)
    web.edb.delete.assert_not_called()


@pytest.mark.asyncio
async def test_patch_rejects_media_from_another_hunt(web):
    """The id comes from the client. The media route is hunt-visibility gated,
    so attaching a foreign media row here is the one way to read past it."""
    with patch("bot.store.MediaJsonDb") as mdb:
        mdb.get_by_id.return_value = SimpleNamespace(id=77, hunt_id=99)
        resp = await web.client.patch("/api/evidence/5", json={"media_id": 77})

    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "cross_hunt_media"
    web.edb.update_media.assert_not_called()


@pytest.mark.asyncio
async def test_patch_rejects_a_media_id_that_does_not_exist(web):
    with patch("bot.store.MediaJsonDb") as mdb:
        mdb.get_by_id.return_value = None
        resp = await web.client.patch("/api/evidence/5", json={"media_id": 12345})

    assert resp.status_code == 400
    web.edb.update_media.assert_not_called()


@pytest.mark.asyncio
async def test_patch_can_detach_the_image_without_a_lookup(web):
    """An explicit null detaches, leaving a text-only item. There is no media to
    validate in that case, so it must not 400 on a missing row."""
    detached = SimpleNamespace(id=5, hunt_id=10, source_puzzle_id=1,
                               target_puzzle_id=None, note="old", media_id=None)
    web.edb.update_media.return_value = detached

    with patch("bot.store.MediaJsonDb") as mdb:
        resp = await web.client.patch("/api/evidence/5", json={"media_id": None})

    assert resp.status_code == 200
    mdb.get_by_id.assert_not_called()
    web.edb.update_media.assert_called_once_with(5, None)


@pytest.mark.asyncio
async def test_a_note_only_patch_leaves_the_image_alone(web):
    """The key must be ABSENT to mean 'no change'. Treating a missing key as
    None would silently strip the image off every note edit."""
    resp = await web.client.patch("/api/evidence/5", json={"note": "fresh"})

    assert resp.status_code == 200
    web.edb.update_media.assert_not_called()

"""Region CRUD and the region-role <-> evidence-row coupling.

DAOs are patched at the service's own import site per docs/agents/python.md:
canvas.py does `from bot.store import ...` at module load, binding its own names.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from bot.core import canvas
from bot.core.canvas_shape import ShapeRejected
from bot.store.canvas_data import CanvasRegionData

RECT = {"type": "rect", "points": [[0.1, 0.2], [0.3, 0.4]]}


def _region(region_id=5, puzzle_id=None, role=None, canvas_id=2):
    r = CanvasRegionData()
    r.id, r.canvas_id = region_id, canvas_id
    r.shape = json.dumps(RECT)
    r.puzzle_id, r.role = puzzle_id, role
    return r


@pytest.fixture
def dbs():
    regions, evidence, images = MagicMock(), MagicMock(), MagicMock()
    # log() returns the row so the service can set_target on it, as log_clues does.
    evidence.log.return_value = MagicMock(id=77)
    with patch.object(canvas, "CanvasRegionJsonDb", regions), \
         patch.object(canvas, "EvidenceJsonDb", evidence), \
         patch.object(canvas, "puzzle_images", images):
        yield regions, evidence, images


def test_creating_an_unlinked_region_stores_normalised_geometry(dbs):
    regions, evidence, images = dbs
    regions.create.return_value = _region()
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT)
    assert json.loads(regions.create.call_args.kwargs["shape"]) == RECT
    assert regions.create.call_args.kwargs["role"] is None
    evidence.log.assert_not_called()


def test_a_locates_region_creates_no_evidence(dbs):
    """locates means the region IS the puzzle. There is no fact to record."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(puzzle_id=9, role="locates")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates")
    evidence.log.assert_not_called()


def test_a_locates_region_syncs_its_puzzle_image(dbs):
    regions, evidence, images = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="locates")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates", media_id=123)
    images.sync_region_image.assert_called_once_with(5, 9, "locates", 123)


def test_creating_a_locates_region_stores_its_crop_on_the_region(dbs):
    """The column records the region's own crop, not the evidence's."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="locates")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates", media_id=123)
    assert regions.create.call_args.kwargs["crop_media_id"] == 123


def test_an_informs_region_creates_a_real_evidence_row(dbs):
    """So routing, the evidence panel and the contribution table need no special
    cases: an informs-region IS a piece of evidence about its target."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="informs")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT, note="TRANSCRIPTION",
                         puzzle_id=9, role="informs")
    evidence.log.assert_called_once()
    args, kwargs = evidence.log.call_args
    assert args[0] == 1                     # hunt_id
    assert args[1] is None                  # source_puzzle_id: a picture, not a puzzle
    assert args[2] == "TRANSCRIPTION"
    assert kwargs["source_region_id"] == 5
    # Target is set the way log_clues does it, not via a log() kwarg.
    evidence.set_target.assert_called_once_with(77, 9)


def test_an_informs_region_forwards_a_caller_supplied_media_id(dbs):
    """The crop is the caller's job (an async, I/O-bound step this
    lock-free/synchronous module does not do) -- this just has to pass it on."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="informs")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs", media_id=123)
    assert evidence.log.call_args.kwargs["media_id"] == 123


def test_an_informs_region_still_forwards_to_sync_which_removes_any_image_row(dbs):
    """canvas.py never decides for itself whether a row should exist --
    sync_region_image (tested on its own in test_puzzle_images.py) owns that;
    here we only confirm the informs role and media are forwarded to it."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="informs")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs", media_id=123)
    images.sync_region_image.assert_called_once_with(5, 9, "informs", 123)


def test_an_informs_region_still_stores_its_crop_on_the_region(dbs):
    """crop_media_id records the region's crop for either role."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(region_id=5, puzzle_id=9, role="informs")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs", media_id=123)
    assert regions.create.call_args.kwargs["crop_media_id"] == 123


def test_an_informs_region_creates_no_informs_edge(dbs):
    """An informs EDGE joins two puzzles, and a region is not one. The target
    gains a contribution row without gaining a member — intended."""
    regions, evidence, images = dbs
    regions.create.return_value = _region(puzzle_id=9, role="informs")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs")
    assert not hasattr(evidence, "link_informs_for_evidence") or \
        not evidence.link_informs_for_evidence.called


@pytest.mark.parametrize("puzzle_id,role", [
    (9, "banana"),      # unknown role
    (None, "informs"),  # role without a puzzle
    (9, None),          # puzzle without a role
])
def test_a_half_set_or_invalid_link_is_rejected(dbs, puzzle_id, role):
    """role is NULL exactly when puzzle_id is NULL — the schema's invariant, with
    no check constraint to enforce it."""
    with pytest.raises(canvas.RegionRejected):
        canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                             puzzle_id=puzzle_id, role=role)


def test_bad_geometry_propagates(dbs):
    with pytest.raises(ShapeRejected):
        canvas.create_region(canvas_id=2, hunt_id=1,
                             shape={"type": "rect", "points": [[0.5, 0.1], [0.5, 0.9]]})


def test_changing_informs_to_locates_deletes_the_evidence_row(dbs):
    """The evidence row exists BECAUSE of the informs link. Leaving it would
    strand a fact about a puzzle the region no longer informs."""
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates")
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.log.assert_not_called()


def test_changing_locates_to_informs_syncs_the_puzzle_image(dbs):
    """Relinking away from locates must drop the image row -- delegated to
    sync_region_image, which canvas.py must actually call on this link change."""
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs")
    images.sync_region_image.assert_called_once_with(5, 9, "informs", None)


def test_clearing_the_link_syncs_the_puzzle_image(dbs):
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=None, role=None)
    images.sync_region_image.assert_called_once_with(5, None, "", None)


def test_an_unchanged_locates_link_does_not_touch_the_puzzle_image(dbs):
    """A note/label edit with the link unchanged must not call sync_region_image
    at all -- the image row's position must not be disturbed."""
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT, label="moved",
                         puzzle_id=9, role="locates")
    images.sync_region_image.assert_not_called()


def test_relinking_an_informs_region_replaces_its_evidence_row(dbs):
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=11, role="informs")
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.set_target.assert_called_once_with(77, 11)


def test_clearing_the_link_deletes_the_evidence_row(dbs):
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=None, role=None)
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.log.assert_not_called()


def test_locates_to_informs_creates_the_evidence_row(dbs):
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs")
    assert evidence.log.call_args.kwargs["source_region_id"] == 5


def test_relinking_an_informs_region_forwards_a_caller_supplied_media_id(dbs):
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=11, role="informs", media_id=456)
    assert evidence.log.call_args.kwargs["media_id"] == 456


def test_an_unchanged_informs_link_keeps_its_evidence_row(dbs):
    """Editing only the note or the rectangle must not churn the evidence row --
    deleting and recreating it would change its id and lose its place in the
    contribution table."""
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT, label="moved",
                         puzzle_id=9, role="informs")
    evidence.delete_by_region.assert_not_called()
    evidence.log.assert_not_called()


def test_updating_a_region_stores_its_crop_on_the_region(dbs):
    regions, evidence, images = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates", media_id=456)
    assert regions.update.call_args.kwargs["crop_media_id"] == 456


def test_a_note_edit_keeps_the_regions_existing_crop(dbs):
    """An absent crop means "no new crop", not "clear it". The caller only
    crops when a link is CHANGING, so writing None through on a note edit
    would orphan the crop and disarm the never-re-crop-under-ink guard."""
    regions, _, _ = dbs
    existing = _region(puzzle_id=9, role="locates")
    existing.crop_media_id = 456
    regions.get_by_id.return_value = existing
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT, note="a note",
                         puzzle_id=9, role="locates", media_id=None)
    assert regions.update.call_args.kwargs["crop_media_id"] == 456


def test_a_suppressed_recrop_syncs_the_existing_crop(dbs):
    """The DB write resolves crop_media_id = existing when media_id is None;
    sync_region_image must receive that SAME resolved id. Otherwise a relinked
    locates region whose re-crop was suppressed (ink guard, or a failed crop)
    is deleted from the old puzzle's strip and never recreated on the new one,
    while its crop still exists in the DB. (Review 2026-07-28.)"""
    regions, evidence, images = dbs
    existing = _region(puzzle_id=9, role="locates")
    existing.crop_media_id = 42
    regions.get_by_id.return_value = existing

    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=11, role="locates")   # relink 9 -> 11, no new crop

    images.sync_region_image.assert_called_once_with(5, 11, "locates", 42)
    assert regions.update.call_args.kwargs["crop_media_id"] == 42


def test_updating_a_missing_region_is_rejected(dbs):
    regions, _, _ = dbs
    regions.get_by_id.return_value = None
    with pytest.raises(canvas.RegionRejected):
        canvas.update_region(region_id=5, hunt_id=1, shape=RECT)


def test_deleting_a_region_relies_on_the_fk_cascade(dbs):
    """No explicit evidence delete: evidence_items.source_region_id is ON DELETE
    CASCADE, so a second delete here would be dead code pretending to matter."""
    regions, evidence, images = dbs
    canvas.delete_region(5)
    regions.delete.assert_called_once_with(5)
    evidence.delete_by_region.assert_not_called()


class TestRegionDisplayNames:
    """The label -> canvas name -> "Canvas (filename)" -> "Canvas <id>" chain.

    Composed in Python rather than as a SQL CASE precisely so the branch a row
    lands on is testable without a database.
    """

    def _row(self, **kw):
        base = {"id": 1, "label": "", "canvas_id": 7,
                "canvas_name": "", "filename": ""}
        base.update(kw)
        return base

    def test_a_label_wins_over_everything_else(self):
        from bot.store.mysqldb import _region_display_name
        row = self._row(label="Hex maze", canvas_name="Big sheet", filename="sheet.png")
        assert _region_display_name(row) == "Hex maze"

    def test_no_label_falls_back_to_the_canvas_name(self):
        from bot.store.mysqldb import _region_display_name
        row = self._row(canvas_name="Big sheet", filename="sheet.png")
        assert _region_display_name(row) == "Big sheet"

    def test_no_label_and_an_unnamed_canvas_falls_back_to_the_filename(self):
        """hunt_canvases.name defaults to '' and nothing forces a rename, so
        this is the COMMON case, not an edge one. It used to render blank."""
        from bot.store.mysqldb import _region_display_name
        assert _region_display_name(self._row(filename="sheet.png")) == "Canvas (sheet.png)"

    def test_whitespace_only_values_do_not_count_as_names(self):
        from bot.store.mysqldb import _region_display_name
        row = self._row(label="   ", canvas_name="\t", filename="sheet.png")
        assert _region_display_name(row) == "Canvas (sheet.png)"

    def test_nothing_at_all_still_says_canvas(self):
        """A blank Source cell reads as 'no source', which is a different and
        wrong claim about the item."""
        from bot.store.mysqldb import _region_display_name
        assert _region_display_name(self._row(canvas_id=7)) == "Canvas 7"

    def test_a_null_filename_from_the_left_join_does_not_crash(self):
        from bot.store.mysqldb import _region_display_name
        row = self._row(canvas_name=None, filename=None, canvas_id=7)
        assert _region_display_name(row) == "Canvas 7"

    def test_get_names_by_ids_maps_each_id_to_its_own_name(self):
        """Two rows landing on DIFFERENT branches of the chain: a version that
        applied one row's name to the whole batch, or transposed the mapping,
        would still pass a single-row test."""
        from bot.store.mysqldb import MySQLCanvasRegionDb
        mydb, cursor = MagicMock(), MagicMock()
        mydb.dict_cursor.return_value = cursor
        cursor.fetchall.return_value = [
            {"id": 3, "label": "Hex maze", "canvas_id": 7,
             "canvas_name": "", "filename": "sheet.png"},
            {"id": 4, "label": "", "canvas_id": 7,
             "canvas_name": "", "filename": "sheet.png"},
        ]

        out = MySQLCanvasRegionDb(mydb=mydb).get_names_by_ids([3, 4])

        assert out == {3: "Hex maze", 4: "Canvas (sheet.png)"}
        # The filename can only come from the media join.
        sql = cursor.execute.call_args.args[0]
        assert "LEFT JOIN media" in sql

    def test_an_empty_id_list_runs_no_query(self):
        from bot.store.mysqldb import MySQLCanvasRegionDb
        mydb, cursor = MagicMock(), MagicMock()
        mydb.dict_cursor.return_value = cursor

        assert MySQLCanvasRegionDb(mydb=mydb).get_names_by_ids([None, 0]) == {}
        cursor.execute.assert_not_called()

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
    regions, evidence = MagicMock(), MagicMock()
    # log() returns the row so the service can set_target on it, as log_clues does.
    evidence.log.return_value = MagicMock(id=77)
    with patch.object(canvas, "CanvasRegionJsonDb", regions), \
         patch.object(canvas, "EvidenceJsonDb", evidence):
        yield regions, evidence


def test_creating_an_unlinked_region_stores_normalised_geometry(dbs):
    regions, evidence = dbs
    regions.create.return_value = _region()
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT)
    assert json.loads(regions.create.call_args.kwargs["shape"]) == RECT
    assert regions.create.call_args.kwargs["role"] is None
    evidence.log.assert_not_called()


def test_a_locates_region_creates_no_evidence(dbs):
    """locates means the region IS the puzzle. There is no fact to record."""
    regions, evidence = dbs
    regions.create.return_value = _region(puzzle_id=9, role="locates")
    canvas.create_region(canvas_id=2, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates")
    evidence.log.assert_not_called()


def test_an_informs_region_creates_a_real_evidence_row(dbs):
    """So routing, the evidence panel and the contribution table need no special
    cases: an informs-region IS a piece of evidence about its target."""
    regions, evidence = dbs
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


def test_an_informs_region_creates_no_informs_edge(dbs):
    """An informs EDGE joins two puzzles, and a region is not one. The target
    gains a contribution row without gaining a member — intended."""
    regions, evidence = dbs
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
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="locates")
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.log.assert_not_called()


def test_relinking_an_informs_region_replaces_its_evidence_row(dbs):
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=11, role="informs")
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.set_target.assert_called_once_with(77, 11)


def test_clearing_the_link_deletes_the_evidence_row(dbs):
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=None, role=None)
    evidence.delete_by_region.assert_called_once_with(5)
    evidence.log.assert_not_called()


def test_locates_to_informs_creates_the_evidence_row(dbs):
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="locates")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=9, role="informs")
    assert evidence.log.call_args.kwargs["source_region_id"] == 5


def test_an_unchanged_informs_link_keeps_its_evidence_row(dbs):
    """Editing only the note or the rectangle must not churn the evidence row --
    deleting and recreating it would change its id and lose its place in the
    contribution table."""
    regions, evidence = dbs
    regions.get_by_id.return_value = _region(puzzle_id=9, role="informs")
    canvas.update_region(region_id=5, hunt_id=1, shape=RECT, label="moved",
                         puzzle_id=9, role="informs")
    evidence.delete_by_region.assert_not_called()
    evidence.log.assert_not_called()


def test_updating_a_missing_region_is_rejected(dbs):
    regions, _ = dbs
    regions.get_by_id.return_value = None
    with pytest.raises(canvas.RegionRejected):
        canvas.update_region(region_id=5, hunt_id=1, shape=RECT)


def test_deleting_a_region_relies_on_the_fk_cascade(dbs):
    """No explicit evidence delete: evidence_items.source_region_id is ON DELETE
    CASCADE, so a second delete here would be dead code pretending to matter."""
    regions, evidence = dbs
    canvas.delete_region(5)
    regions.delete.assert_called_once_with(5)
    evidence.delete_by_region.assert_not_called()

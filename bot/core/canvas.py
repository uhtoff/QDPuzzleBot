"""Canvas regions, and the coupling between a region's link and its evidence.

A new module rather than more of puzzle_service.py, which is already ~2,900 lines
and is a documented contention magnet.

Lock-free by the bot/core convention: callers lock. In particular the
create-puzzle-from-region path (B2b) must not take a lock that the puzzle create
path will take again internally.
"""
import logging

from bot.core.canvas_shape import parse_shape
from bot.store import CanvasRegionJsonDb, EvidenceJsonDb

logger = logging.getLogger(__name__)

LOCATES = "locates"
INFORMS = "informs"
ROLES = (LOCATES, INFORMS)


class RegionRejected(Exception):
    """A region's link was malformed. The message is safe to show a user."""


def _check_link(puzzle_id, role):
    """role is set exactly when puzzle_id is — the schema's stated invariant.

    Enforced here rather than by a check constraint: this schema uses none, and a
    half-set link would otherwise render as a linked region that tints by nothing,
    or an unlinked one that claims a role.
    """
    if puzzle_id is None and role is None:
        return
    if puzzle_id is None or role is None:
        raise RegionRejected("A linked region needs both a puzzle and a role.")
    if role not in ROLES:
        raise RegionRejected(f"Role must be one of: {', '.join(ROLES)}.")


def _log_informs_evidence(hunt_id, region_id, puzzle_id, note, media_id=None):
    """An informs-region IS a piece of evidence about its target.

    Creating a real evidence_items row means routing, the evidence panel and the
    contribution table all work with no special cases. source_puzzle_id stays
    NULL: the provenance is a picture, not a puzzle.

    No informs EDGE is created either — an edge joins two puzzles and a region is
    not one. The consequence is deliberate: the target gains a contribution row
    without gaining a member, and such a row cannot be reached by the subtree
    walk, which follows feeds edges only.

    Target is set with a follow-up set_target rather than a log() argument,
    because that is how log_clues already does it.
    """
    item = EvidenceJsonDb.log(
        hunt_id, None, note or "",
        media_id=media_id,
        source_region_id=region_id,
    )
    return EvidenceJsonDb.set_target(item.id, puzzle_id) or item


def create_region(canvas_id: int, hunt_id: int, shape, label: str = "",
                  note=None, puzzle_id=None, role=None):
    """Create one region, and its evidence row if it informs a puzzle."""
    _check_link(puzzle_id, role)
    stored = parse_shape(shape)
    region = CanvasRegionJsonDb.create(
        canvas_id=canvas_id, shape=stored, label=label, note=note,
        puzzle_id=puzzle_id, role=role)
    if role == INFORMS:
        _log_informs_evidence(hunt_id, region.id, puzzle_id, note)
    return region


def update_region(region_id: int, hunt_id: int, shape, label: str = "",
                  note=None, puzzle_id=None, role=None):
    """Update one region, keeping its evidence row in step with its link.

    A change to the link — its role, its target, or clearing it — drops the old
    evidence row and recreates it only if the new link still informs. One
    function owning both sides is what stops a region and its evidence drifting
    apart.

    An unchanged link is left alone: churning the row on a note or geometry edit
    would change its id and lose its place in the contribution table.
    """
    _check_link(puzzle_id, role)
    existing = CanvasRegionJsonDb.get_by_id(region_id)
    if existing is None:
        raise RegionRejected("That region no longer exists.")
    stored = parse_shape(shape)
    link_changed = (existing.puzzle_id != puzzle_id or existing.role != role)
    CanvasRegionJsonDb.update(region_id, shape=stored, label=label, note=note,
                              puzzle_id=puzzle_id, role=role)
    if link_changed and existing.role == INFORMS:
        EvidenceJsonDb.delete_by_region(region_id)
    if link_changed and role == INFORMS:
        _log_informs_evidence(hunt_id, region_id, puzzle_id, note)
    return CanvasRegionJsonDb.get_by_id(region_id)


def delete_region(region_id: int) -> None:
    """Delete a region.

    Its evidence rows go with it: evidence_items.source_region_id is ON DELETE
    CASCADE, so deleting them here as well would be dead code that looks
    load-bearing.
    """
    CanvasRegionJsonDb.delete(region_id)

"""A puzzle's ordered set of images: canvas `locates` crops plus direct
uploads.

Lock-free by the bot/core convention: callers lock.

`LOCATES` is redefined here rather than imported from `bot.core.canvas` —
canvas.py imports this module (to call `sync_region_image` after a region
edit), so importing canvas.py from here would be circular.
"""
import logging

from bot.store import PuzzleImageJsonDb

logger = logging.getLogger(__name__)

LOCATES = "locates"


class ImageNotFound(Exception):
    """No such image on that puzzle. The message is safe to show a user."""


class BadReorder(Exception):
    """The new order isn't a permutation of the puzzle's images. The message is
    safe to show a user."""


def list_images(puzzle_id: int) -> list:
    """The puzzle's images, in the DAO's own order."""
    return PuzzleImageJsonDb.get_by_puzzle(puzzle_id)


def attach_upload(puzzle_id: int, media_id: int):
    """Attach a directly-uploaded image, appended after every existing one."""
    existing = PuzzleImageJsonDb.get_by_puzzle(puzzle_id)
    return PuzzleImageJsonDb.create(
        puzzle_id=puzzle_id, media_id=media_id, source_region_id=None,
        position=len(existing))


def reorder(puzzle_id: int, ordered_ids: list) -> None:
    """Rewrite the puzzle's display order to exactly `ordered_ids`.

    The ids are client-supplied, so every one of them is checked against the
    puzzle before anything is written — a stray id from another puzzle must
    not let a caller reorder (or expose the existence of) images it doesn't own.

    It must also be a full permutation, not merely a subset of owned ids:
    set_positions renumbers only the rows it is given, so a short or duplicated
    list leaves the unlisted images sharing positions with the listed ones, and
    the strip's order becomes whatever the DAO's tie-break happens to be.
    """
    owned_ids = {img.id for img in PuzzleImageJsonDb.get_by_puzzle(puzzle_id)}
    for image_id in ordered_ids:
        if image_id not in owned_ids:
            raise ImageNotFound("That image no longer exists.")
    if len(ordered_ids) != len(owned_ids) or set(ordered_ids) != owned_ids:
        raise BadReorder("Reorder must list every image exactly once.")
    PuzzleImageJsonDb.set_positions(ordered_ids)


def _owned(image_id: int, puzzle_id: int):
    """The image, if it belongs to that puzzle.

    Checked here and not only in the route: an image id is client-supplied,
    and this is the one place both ids are in scope.
    """
    existing = PuzzleImageJsonDb.get_by_id(image_id)
    if existing is None or existing.puzzle_id != puzzle_id:
        raise ImageNotFound("That image no longer exists.")
    return existing


def detach(image_id: int, puzzle_id: int) -> None:
    _owned(image_id, puzzle_id)
    PuzzleImageJsonDb.delete(image_id)


def sync_region_image(region_id: int, puzzle_id, role: str, media_id) -> None:
    """Keep a canvas region's puzzle_images row in step with its role.

    Called after any region edit. Never raises: a crop that failed to
    generate, or hasn't yet, must not fail the region link that triggered it.
    """
    if puzzle_id is None or role != LOCATES:
        PuzzleImageJsonDb.delete_by_region(region_id)
        return
    existing = PuzzleImageJsonDb.get_by_region(region_id)
    if existing is not None:
        if existing.puzzle_id == puzzle_id:
            # The same link as before — leave the row completely alone so it
            # keeps its place in the user's ordering. A note or label edit
            # must not shuffle the strip.
            return
        # Relinked to a DIFFERENT puzzle. The row sits in the old puzzle's
        # strip and cannot stay there: the unique index on source_region_id
        # means there is only ever one row, so leaving it would show this
        # section on the wrong puzzle rather than merely duplicating it.
        PuzzleImageJsonDb.delete_by_region(region_id)
    if media_id is None:
        # No crop yet, or it failed. Nothing to point a row at — and a crop
        # failure must never fail the link that triggered it.
        return
    PuzzleImageJsonDb.create(
        puzzle_id=puzzle_id, media_id=media_id, source_region_id=region_id,
        # Appended, like a direct upload: a section arriving from the canvas
        # should land at the end of the strip rather than jumping to the front
        # of an order someone chose.
        position=len(PuzzleImageJsonDb.get_by_puzzle(puzzle_id)))

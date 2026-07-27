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
    """
    owned_ids = {img.id for img in PuzzleImageJsonDb.get_by_puzzle(puzzle_id)}
    for image_id in ordered_ids:
        if image_id not in owned_ids:
            raise ImageNotFound("That image no longer exists.")
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
    if media_id is None:
        # No crop yet (or it failed). Leave whatever link already exists
        # alone rather than treat a crop failure as a reason to unlink.
        return
    if PuzzleImageJsonDb.get_by_region(region_id) is not None:
        # Already linked — leave the existing row alone so it keeps its
        # position in the strip.
        return
    PuzzleImageJsonDb.create(
        puzzle_id=puzzle_id, media_id=media_id, source_region_id=region_id,
        position=0)

"""Find and delete media nothing references any more.

Nothing else in puzzbot deletes a `media` row: replaced evidence images are kept
by decision, `delete_canvas` removes a pyramid but leaves the row and the
original, every re-crop makes a new row, and detaching a puzzle image only drops
the join row. Each of those is fine on its own; together they leak disk with no
way back. This is the way back — a manual, per-hunt sweep of rows nothing points
at.

Lock-free by the bot/core convention: callers lock. No lock exists for this and
none is needed — but the safety is NOT that `sweep` re-derives the orphan list
itself. The list is derived once and then acted on row by row, so a row can be
attached to something (evidence, canvas, puzzle image, region crop) after the
list was built but before its own turn in the loop; re-deriving the list up
front does nothing for that row. The actual guard is per-row: each delete
re-asserts the same four reference checks at the moment it runs
(`delete_if_still_orphaned`), so a row claimed mid-sweep fails its own DELETE
and survives untouched -- files included -- to be re-evaluated on the next run.
"""
import datetime
import logging
import shutil
from dataclasses import dataclass

from bot.core.image_tiler import tiles_relpath
from bot.core.message_media import media_root
from bot.store import MediaJsonDb

logger = logging.getLogger(__name__)

# How long a row is left alone regardless of references. media_ingest creates
# the `media` row before the caller attaches it to a puzzle, an evidence item, a
# canvas or a region crop, so a freshly-created row is indistinguishable from an
# orphan. A fixed constant, deliberately: no per-hunt setting until something
# needs one.
GRACE_PERIOD = datetime.timedelta(hours=24)


@dataclass
class SweepStats:
    count: int
    bytes_freed: int


def remove_hunt_media(hunt_id: int) -> None:
    """Delete every file a hunt owns: its uploads and its tile pyramids.

    For hunt deletion, not sweeping. The `media` rows carry ON DELETE CASCADE
    from `hunts`, so by the time the hunt row is gone there is nothing left to
    derive paths from and the sweep above — which works from rows — can never
    reclaim them. Both trees are keyed by hunt id, so this is two rmtrees.

    Best-effort and never raises, matching _remove_files: a hunt delete must not
    fail because a directory was already gone or is not writable.
    """
    root = media_root()
    for relpath in (f"uploads/{hunt_id}", tiles_relpath_root(hunt_id)):
        try:
            shutil.rmtree(root / relpath, ignore_errors=True)
        except Exception:
            logger.exception("could not remove %s for hunt %s", relpath, hunt_id)


def tiles_relpath_root(hunt_id: int) -> str:
    """The directory holding every pyramid for one hunt (parent of tiles_relpath)."""
    return f"tiles/{hunt_id}"


def orphaned_media(hunt_id: int) -> list:
    """The hunt's sweepable media: unreferenced and past the grace period."""
    cutoff = datetime.datetime.utcnow() - GRACE_PERIOD
    return MediaJsonDb.get_orphaned(hunt_id, cutoff)


def _remove_files(media) -> None:
    """Best-effort removal of one row's bytes: pyramid first, then the original.

    Never raises. A file that cannot be removed leaks disk, which is strictly
    better than leaving the row behind to be re-reported by every future sweep —
    the same trade `delete_canvas` already makes for tiles.
    """
    root = media_root()
    if media.tile_status != "none":
        try:
            shutil.rmtree(root / tiles_relpath(media.hunt_id, media.id),
                          ignore_errors=True)
        except Exception:
            logger.exception("could not remove tiles for media %s", media.id)
    try:
        (root / media.path).unlink(missing_ok=True)
    except Exception:
        logger.exception("could not remove file for media %s (%s)",
                         media.id, media.path)


def sweep(hunt_id: int) -> SweepStats:
    """Delete every orphaned media row in the hunt, with its files.

    `bytes_freed` sums `media.size` — the original image only. Pyramid sizes are
    not tracked anywhere and a du-walk per sweep is not worth it, so the number
    is a deliberate undercount and callers should report it as "at least".
    """
    count = 0
    bytes_freed = 0
    for media in orphaned_media(hunt_id):
        if not MediaJsonDb.delete_if_still_orphaned(media.id):
            logger.info("media sweep: media %s claimed mid-sweep, skipping",
                       media.id)
            continue  # claimed mid-sweep -- leave the files alone
        _remove_files(media)
        count += 1
        bytes_freed += media.size or 0
    if count:
        logger.info("media sweep: hunt %s, %d row(s), %d byte(s)",
                    hunt_id, count, bytes_freed)
    return SweepStats(count=count, bytes_freed=bytes_freed)


def format_size(num_bytes: int) -> str:
    """Human-readable byte count for report copy (e.g. "34.2 MB")."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"

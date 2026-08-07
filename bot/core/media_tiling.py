"""Async orchestration around the pure tiler.

Owns two things image_tiler deliberately does not: getting the CPU work off the
event loop, and moving a media row through pending -> ready | failed. Being the
single writer of tile_status is what keeps the row and the pyramid on disk from
drifting apart.
"""
import asyncio
import logging

from bot.core.image_tiler import build_pyramid, tiles_relpath
from bot.core.message_media import media_root
from bot.store.media_data import MediaData

logger = logging.getLogger(__name__)

# Strong references to in-flight tiling tasks. asyncio only holds a weak
# reference to a running task, so one with no other referent can be collected
# mid-flight. The refresh_* create_task calls elsewhere get away with it because
# they finish in milliseconds; tiling a large sheet takes seconds, which is long
# enough for a collection to land in the middle of it.
_tiling_tasks: set = set()

# Media ids with a build in flight. Distinct from _tiling_tasks (which exists to
# keep tasks from being collected): this is what stops two builds racing on one
# destination directory. See schedule_tiling.
_tiling_in_flight: set = set()

# Each pyramid build peaks around 360 MB (PNG has no partial decode in libvips,
# so a large sheet is fully resident before it is tiled). Two bounds the
# worst-case footprint while still letting a small crop overlap a big sheet
# rather than queueing behind it. Without a cap, a batch upload or a retile of
# several canvases fans out one build per image and takes the box down.
MAX_CONCURRENT_TILING = 2

# Safe to construct at import on 3.12+: the semaphore binds a loop lazily, on
# first acquire, not here.
_tiling_slots = asyncio.Semaphore(MAX_CONCURRENT_TILING)


async def run_bounded_image_work(fn, *args):
    """Run a memory-heavy image operation off the loop, under the same cap.

    Pyramid builds are not the only full-decode work: cropping a region out of
    a large sheet decodes the whole thing too (libvips has no partial decode for
    PNG), so it contends for exactly the same memory. Sharing one semaphore is
    what makes MAX_CONCURRENT_TILING a real bound on the box's footprint rather
    than a bound on one code path.
    """
    loop = asyncio.get_running_loop()
    async with _tiling_slots:
        return await loop.run_in_executor(None, fn, *args)


async def tile_media(media: MediaData, db) -> None:
    """Build `media`'s pyramid, then record the outcome on its row.

    Never raises. Tiling is a derived convenience: if it fails the original
    image is still stored and still served by /api/media/<id>, and the frontend
    falls back to it — so letting an exception escape here would take down a
    request, or a background task, over something recoverable.

    `db` is passed in rather than imported so the caller's DAO is the one used,
    and the tests need no patching of module globals.
    """
    src = media_root() / media.path
    dest = media_root() / tiles_relpath(media.hunt_id, media.id)
    loop = asyncio.get_running_loop()
    try:
        # run_in_executor, because build_pyramid is seconds of synchronous CPU
        # and this coroutine shares its loop with the Discord gateway. The
        # semaphore wraps only this call, so waiting for a slot costs nothing
        # but memory-bounded queueing.
        async with _tiling_slots:
            width, height, levels = await loop.run_in_executor(
                None, build_pyramid, src, dest)
    except Exception:
        logger.exception("tiling failed for media %s (%s)", media.id, media.path)
        db.update_tiles(media.id, "failed", 0)
        return
    db.update_tiles(media.id, "ready", levels)
    logger.info("media %s tiled: %dx%d, %d levels", media.id, width, height, levels)


def schedule_tiling(media: MediaData, db, after=None) -> bool:
    """Fire off `tile_media(media, db)` as a background task, then optionally
    `await after()` once it finishes. Returns False if a build for this media
    id is already in flight, having scheduled nothing.

    `after` is how a caller learns tiling is done without polling: the web
    upload route and the Discord clue-attachment path both want to push a
    "board changed" notification afterwards, but only once the pyramid is
    actually ready, not at the moment the row is created.

    The in-flight guard lives here rather than at the /retile route because it
    is build_pyramid's contract that needs it: it rmtree's its destination
    before writing, so two concurrent builds for one media id wipe the
    directory under each other and their update_tiles calls land in arbitrary
    order — leaving the row claiming 'ready' over a half-written pyramid.
    Guarding the seam covers every caller, not just the one that raced today.
    """
    if media.id in _tiling_in_flight:
        logger.info("tiling already in flight for media %s; not scheduling again",
                    media.id)
        return False
    _tiling_in_flight.add(media.id)

    async def _run():
        try:
            await tile_media(media, db)
            if after is not None:
                await after()
        finally:
            # In a finally: tile_media never raises, but `after` can, and a
            # media id left in the set would be unbuildable until restart.
            _tiling_in_flight.discard(media.id)

    task = asyncio.create_task(_run())
    _tiling_tasks.add(task)
    task.add_done_callback(_tiling_tasks.discard)
    return True

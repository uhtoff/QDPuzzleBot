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
        # and this coroutine shares its loop with the Discord gateway.
        width, height, levels = await loop.run_in_executor(
            None, build_pyramid, src, dest)
    except Exception:
        logger.exception("tiling failed for media %s (%s)", media.id, media.path)
        db.update_tiles(media.id, "failed", 0)
        return
    db.update_tiles(media.id, "ready", levels)
    logger.info("media %s tiled: %dx%d, %d levels", media.id, width, height, levels)

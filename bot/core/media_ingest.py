"""One shared seam for turning uploaded bytes into a `media` row.

Before this, three call sites (the web upload route, the Discord `!clue`
attachment path, and the canvas region-crop path) each re-implemented
save + probe + needs_tiling + create by hand. They drifted: the Discord path
never called probe_dimensions or needs_tiling at all, so clue images were
stored at 0x0 and never tiled. One seam means there is only one place left
for that to happen again.
"""
import logging

from bot.core.image_tiler import exceeds_pixel_limit, needs_tiling, probe_dimensions
from bot.core.media_store import MediaRejected, save_image_bytes
from bot.core.message_media import media_root

logger = logging.getLogger(__name__)


def ingest_image_bytes(hunt_id: int, filename: str, raw: bytes, origin: str, db,
                       enforce_size_limit: bool = True):
    """Validate+save `raw`, probe its dimensions, and create its `media` row.

    Raises MediaRejected (from save_image_bytes) if the bytes are too large or
    not an allowed image type; creates no row in that case.

    `enforce_size_limit=False` skips only the upload byte cap, for bytes this
    server generated (see validate_image_bytes). The pixel ceiling below still
    applies — that one is about what we can safely decode, not about trust.
    """
    saved = save_image_bytes(
        hunt_id=hunt_id, filename=filename, raw=raw, origin=origin,
        enforce_size_limit=enforce_size_limit,
    )
    try:
        width, height = probe_dimensions(media_root() / saved["path"])
    except Exception:
        # save_image_bytes already sniffed the magic bytes, so this is a valid
        # image of an allowed type that libvips still cannot open. Keep the
        # upload rather than lose it; it simply gets no pyramid.
        logger.exception("could not probe dimensions for %s", saved["path"])
        width, height = 0, 0
    # Dimensions are only known after the bytes are on disk (the probe reads the
    # header from the file), so an over-large image is rejected here rather than
    # in validate_image_bytes — which means cleaning up the file we just wrote.
    if exceeds_pixel_limit(width, height):
        try:
            (media_root() / saved["path"]).unlink()
        except OSError:
            logger.exception("could not remove oversize upload %s", saved["path"])
        raise MediaRejected(
            f"Image too large: {width}x{height} pixels exceeds the limit."
        )
    tiled = needs_tiling(width, height)
    return db.create(
        hunt_id=hunt_id, path=saved["path"], filename=saved["filename"],
        content_type=saved["content_type"], size=saved["size"], origin=saved["origin"],
        width=width, height=height,
        tile_status="pending" if tiled else "none",
    )

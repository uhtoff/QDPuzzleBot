"""Tile pyramid generation for images too large for a browser to decode.

Pure by design: a source path and a destination directory in, dimensions and a
level count out. No DAOs, no Quart, no discord.py — so it unit-tests against
nothing but a file on disk, and the async orchestration lives next door in
media_tiling.py.

Every constant here was measured on the image this feature exists for
(14400x14400, 207 MP); see the "Tiling" section of
docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Set BEFORE pyvips is imported anywhere: libvips reads VIPS_CONCURRENCY at
# initialisation, and pyvips 3.1.1 exposes no concurrency_set() to change it
# afterwards. This module is the only importer of pyvips and it imports it
# lazily inside the functions below, so module scope is early enough.
#
# Measured on the 207 MP image: 13.6 s/224 MB at 1 thread, 8.1 s/340 MB at 2,
# 6.7 s/563 MB at 4, 6.9 s/982 MB unbounded on 12 cores. Past two threads it
# buys about a second for triple the peak memory, which is the wrong trade on a
# small VPS. setdefault, not assignment, so an operator can still override it.
os.environ.setdefault("VIPS_CONCURRENCY", "2")

TILE_SIZE = 512

# Lossless, not lossy. On flat white line art lossless is both smaller (9.6 MB
# against 11 MB at Q82 for the motivating image) and exact — and this content is
# small black lettering that has to stay readable at native zoom, which is
# precisely where lossy compression rings.
TILE_SUFFIX = ".webp[lossless=true]"
TILE_EXT = ".webp"

# dzsave omits a tile that is entirely blank and writes this alongside the
# pyramid. NOT what gets served, though — see blank_tile_bytes() below.
BLANK_TILE_NAME = "blank.png"

_blank_tile_cache: "bytes | None" = None


def blank_tile_bytes() -> bytes:
    """A plain opaque white TILE_SIZE² tile, built once and cached in memory.

    dzsave's own blank.png — the file BLANK_TILE_NAME names — is NOT this: it
    is a degenerate single-band, near-zero-value placeholder that does not
    reflect the omitted tile's actual colour (confirmed by inspecting a real
    build_pyramid() output). Serving it verbatim renders every "uniform
    colour, nothing here" hole in the grid as solid black, in any theme —
    exactly the spot a mostly-white puzzle sheet has the most of. Nearly every
    canvas is white paper, so a real white tile is the correct fallback.
    """
    global _blank_tile_cache
    if _blank_tile_cache is None:
        import pyvips
        # pyvips builds its API at runtime from libvips introspection, so no
        # static checker can see these signatures — same reasoning as the
        # dzsave() suppression in build_pyramid() below.
        img = pyvips.Image.black(TILE_SIZE, TILE_SIZE, bands=3) + 255  # pyright: ignore[reportOperatorIssue]
        buf = img.write_to_buffer(".png")  # pyright: ignore[reportOptionalCall, reportCallIssue, reportAttributeAccessIssue]
        assert isinstance(buf, bytes)
        _blank_tile_cache = buf
    return _blank_tile_cache

# Above this many pixels an image gets a pyramid. 16 MP is about where mobile
# Safari stops being willing to decode an image at all, and a 4000x4000 sheet is
# already awkward on a laptop. Below it a pyramid would be hundreds of files
# serving no purpose — a screenshot is fine as a single <img>.
MAX_DIRECT_PIXELS = 16_000_000


def needs_tiling(width: int, height: int) -> bool:
    """Whether an image of this size should get a pyramid.

    Keyed on area rather than on either side: a 20000x100 panorama decodes
    fine, and it is total pixels that exhaust a decoder.
    """
    return width * height > MAX_DIRECT_PIXELS


def tiles_relpath(hunt_id: int, media_id: int) -> str:
    """Directory, relative to media_root(), holding one row's pyramid.

    Keyed by media id, not by the upload's filename: regeneration is then
    idempotent (remove the directory and rebuild) and deleting a media row
    deletes exactly one directory. A uuid-keyed path would leave orphans that
    nothing could ever sweep with confidence.
    """
    return f"tiles/{hunt_id}/{media_id}"


def tile_relpath(hunt_id: int, media_id: int, z: int, y: int, x: int) -> str:
    """One tile's path, relative to media_root().

    z/y/x — row before column — because that is libvips' "google" layout. The
    order is asserted in the tests with y != x, since transposing it would flip
    the image about its diagonal and look almost plausible.
    """
    return f"{tiles_relpath(hunt_id, media_id)}/{z}/{y}/{x}{TILE_EXT}"


def _load(pyvips, src: Path):
    """Open `src` for streaming, narrowed to a single Image.

    pyvips annotates new_from_file as `Image | list | None` because it can also
    load multi-page documents. Only single images reach here — the upload path
    sniffs the magic bytes and allows PNG/JPEG/GIF/WebP only — so the union is
    narrowed once, here, rather than asserted at every use site.
    """
    im = pyvips.Image.new_from_file(str(src), access="sequential")
    if not isinstance(im, pyvips.Image):
        raise ValueError(f"not a single-page image: {src.name}")
    return im


def _dims(im) -> tuple[int, int]:
    """(width, height) as plain ints.

    pyvips resolves image properties through a dynamic __getattr__, so it types
    them as a wide union rather than as ints. Narrowed once here so the coercion
    and its suppression do not have to be repeated at every use site.
    """
    return int(im.width), int(im.height)  # pyright: ignore[reportArgumentType]


def probe_dimensions(src: Path) -> tuple[int, int]:
    """Read (width, height) from the header without decoding the pixels."""
    import pyvips
    return _dims(_load(pyvips, src))


def build_pyramid(src: Path, dest_dir: Path) -> tuple[int, int, int]:
    """Write a tile pyramid for `src` into `dest_dir`.

    Returns (width, height, levels). The deepest level index is levels - 1 and
    is native resolution.

    Synchronous and CPU-bound — eight seconds on the motivating image — so
    callers MUST run it off the event loop. media_tiling.tile_media is the
    supported way in.

    Raises on a corrupt or unreadable source. libvips creates no output
    directory when the load fails, so a failure leaves nothing behind; a stale
    pyramid from an earlier successful run is removed first.
    """
    import pyvips

    # dzsave will not overwrite an existing tree, and a rebuild after the source
    # shrank would otherwise leave deeper levels from the previous image in
    # place, silently serving tiles that no longer belong to it.
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    im = _load(pyvips, src)
    width, height = _dims(im)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "suffix": TILE_SUFFIX,
        "tile_size": TILE_SIZE,
        "overlap": 0,
        # Keep halving until the whole image fits in a single tile, so level 0 is
        # always a complete thumbnail and the viewer has something to show
        # immediately at any zoom.
        "depth": "onetile",
        "layout": "google",
    }
    # pyvips builds its operation methods, dzsave included, at runtime from
    # libvips introspection, so no static checker can see this signature. The
    # suppression is confined to this one call rather than applied file-wide.
    im.dzsave(str(dest_dir), **opts)  # pyright: ignore[reportOptionalCall, reportCallIssue, reportAttributeAccessIssue]
    levels = len([d for d in dest_dir.iterdir() if d.is_dir() and d.name.isdigit()])
    logger.info("tiled %s (%dx%d) into %d levels", src.name, width, height, levels)
    return width, height, levels

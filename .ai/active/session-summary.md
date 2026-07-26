# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-07-26
Status: in progress — B1 complete (Task 8 @ 53f9055); B2 not started

## Goal

Canvas Plan B from `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md`.
Split into **B1 — tiled media pipeline** (done) and **B2 — the canvas itself** (next).

## Current status

**B1 is complete and committed on `staging`.** All eight plan tasks done, full suite green
(1317 passed), ruff clean, basedpyright 0 errors. Verified on the real 14400×14400 image
through the actual `media_tiling.tile_media` path: `update_tiles(7, 'ready', 6)`, 940 tiles,
6 levels, 9.6 MB, 8.2 s, 359 MB peak.

B1 ships no UI. Nothing consumes tiles until B2.

## Final decisions

- **A canvas image is served as a tile pyramid, not one `<img>`.** The design originally said
  single-`<img>` + CSS transform. The motivating image (Edric's extravaganza sheet, 14400²,
  207 MP, ~830 MB decoded) cannot be displayed that way. Design doc amended in `a6d9248`.
- **`pyvips[binary]`** — the wheel bundles libvips 8.18.4, so it is pip-only with no root and
  no system packages on the Plesk host. This was the blocker against a dependency, and it
  does not apply.
- **Lossless WebP**, because on flat white line art it is both *smaller* (9.6 MB vs 11 MB at
  Q82) and exact. There is no quality/size trade-off to make here.
- **`VIPS_CONCURRENCY=2`**, set at module scope in `image_tiler.py` — libvips reads it at
  init and pyvips 3.1.1 has no `concurrency_set()`. Measured: 13.6 s/224 MB at 1, 8.1 s/340 MB
  at 2, 6.7 s/563 MB at 4, 6.9 s/982 MB unbounded.
- **Tiles keyed by `media_id`**, at `tiles/{hunt_id}/{media_id}/{z}/{y}/{x}.webp`, siblings of
  Plan A's `uploads/`. No `media` rows for tiles.
- **Tiling is off-loop** via `run_in_executor`, row moves `pending → ready | failed`.
- **Only images over 16 MP get a pyramid** (`MAX_DIRECT_PIXELS`), so screenshots do not become
  hundreds of pointless tiles.
- **`asyncio.create_task` + a strong reference set**, matching the codebase's `refresh_*`
  idiom rather than Quart's `add_background_task` (which the plan had suggested). The
  reference set is the deviation: asyncio holds only a weak reference, and ~8 s of tiling is
  long enough for a collection to land mid-run, unlike those millisecond refreshes.

## Rejected approaches

- **Reusing Edric's 4800×4800 slices as tiles.** They are download chunks, not a pyramid:
  23 MP each and no overview level, so a fit-to-screen view still decodes all nine.
- **Client-side tiling at upload.** The browser cannot decode the 207 MP source either.
- **Pillow instead of pyvips.** ~830 MB to decode the source, and `MAX_IMAGE_PIXELS` would
  need raising on a user-upload path.
- **Lossy WebP.** Larger *and* worse for this content.
- **A temp-dir-and-rename for atomicity.** Unnecessary: libvips creates no output directory
  when a load fails, and `build_pyramid` removes a stale pyramid before rebuilding. Accepted
  gap noted in the plan's self-review — a SIGKILL mid-dzsave leaves a partial tree with
  `tile_status='pending'`, which the route 404s, so the viewer falls back to the whole image.

## Next steps

1. Write `docs/superpowers/plans/2026-07-26-canvas-b2-canvas.md`.
2. B2 scope per the design: `hunt_canvases` + `canvas_regions` (migration 033), the canvas
   service in a new `bot/core/canvas.py` (**not** `puzzle_service`, which is ~2,900 lines),
   region CRUD API, `CanvasPanel`/`CanvasView`/`RegionModal`, draw-to-create-puzzle, the
   progress tint overlay, websocket live updates, `!canvas`.
3. The B2 viewer must mount only the tiles intersecting the viewport, and show progress while
   `tile_status` is `pending`.
4. **B2 must `rmtree(tiles_relpath(...))` when a canvas is deleted** — B1 deliberately left
   that out because no single-media delete path exists yet.

## Open questions

- None blocking. See `open-questions.md`.

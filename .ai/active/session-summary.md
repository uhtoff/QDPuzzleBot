# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-07-26
Status: in progress — feat(web): canvas and region API @ aa43fcd

## Goal

Canvas Plan B from `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md`.
Split into **B1 — tiled media pipeline** (done), **B2a — model/service/API** (done), and
**B2b — the viewer, RegionModal, draw-to-create, `!canvas`** (next).

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

## B2a decisions (done)

- **`bot/core/canvas.py` owns both sides of the region-link/evidence coupling.** A role change,
  relink, or cleared link drops the evidence row and recreates it only if the new link still
  informs. An **unchanged** link is left alone — churning the row on a note or geometry edit
  would change its id and lose its place in the contribution table.
- **`evidence_items.source_region_id` is ON DELETE CASCADE**, unlike the sheet (031) and media
  (030) source columns which SET NULL. An informs-region's evidence row is created *by* the
  link and is meaningless without it.
- **No informs edge** for a region source: an edge joins two puzzles, a region is not one.
- **Geometry is validated in a pure module** (`canvas_shape.py`), normalising the drag to
  top-left-first on each axis independently, rejecting out-of-range and zero-area rectangles,
  and refusing booleans as coordinates (bool is an int subclass).
- **Two permission bars.** Canvas create/delete = organiser. Region draw/link/edit = any
  member who can see an unarchived hunt, same as logging evidence. Both pinned by tests that
  go red if collapsed.
- **`DELETE /api/canvases/<id>` rmtrees the tile directory** — this was B1's deferred gap and
  it is now closed.
- **`create-puzzle-from-region` deferred to B2b** on purpose: its arguments (round choice, meta
  type) are decided by UI that does not exist yet.

## Next steps — B2b

1. Write `docs/superpowers/plans/2026-07-26-canvas-b2b-viewer.md`.
2. Frontend: a **Canvas** entry in `HuntBoard.tsx`'s `SegmentedControl` (its width calculation
   is hard-coded and must be updated), `CanvasPanel.tsx` (picker + upload), `CanvasView.tsx`
   (tiled viewer), `RegionModal.tsx`.
3. The viewer must mount **only the tiles intersecting the viewport** at the current level —
   `GET /api/media/<id>/tiles/<z>/<y>/<x>`, google order (row before column) — and show
   progress while `tile_status` is `pending`, falling back to `GET /api/media/<id>` when
   `tile_status` is `none` or `failed`.
4. Progress tint: green `solved`/`backsolved`, amber `in progress`/`needs extraction`/`needs
   submission`, red `stuck`, grey `unstarted`; untinted = unclaimed art. Legend + claimed count.
5. `POST /api/canvases/<id>/regions/<rid>/create-puzzle` — delegate to the existing
   `puzzle_service` create path and then link the region. **Must not take a lock the create
   path takes internally** (`bot/core` is lock-free by convention; callers lock).
6. `!canvas` posts a link; update its docstring **and** `docs/commands.md` in the same change.
   Drawing stays web-only — a deliberate parity exception like the archiving family.

## Open questions

- None blocking. See `open-questions.md`.

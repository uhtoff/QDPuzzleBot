# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-07-26
Status: ready for handoff — evidence OCR feature complete, all automated gates green (backend
suite, ruff, basedpyright, frontend tsc/build); manual browser verification still owed for both
new "Extract text" buttons and for the earlier B2b canvas UI (see "Known gaps and risks")

## Goal

Canvas Plan B from `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md`.
Split into **B1 — tiled media pipeline** (done), **B2a — model/service/API** (done), and
**B2b — viewer, RegionModal, draw-to-create, `!canvas`** (code done, unverified in a browser).

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

## B2b decisions (code done)

- **`CanvasView` mounts only the tiles intersecting the viewport** (plus a one-tile margin) at
  a level chosen as the smallest one still covering the displayed width, so tiles downscale
  rather than blow up. Tile URLs are `z/y/x` — row before column.
- **The SVG overlay is `viewBox="0 0 1 1"`**, so region rects use their stored normalised
  coordinates directly at any zoom. This is the payoff of B2a's coordinate choice.
- **`tile_status` drives the fallbacks**: Loader while `pending`, plain `/api/media/<id>` when
  `none` or `failed`.
- **`meta_type` was dropped from draw-to-create**, not implemented. `create_puzzle` has no meta
  concept — metas are a separate route that builds a category — so offering it would have meant
  inventing behaviour.
- **`create-puzzle-from-region` mirrors `/hunts/<uid>/puzzles`' `hunt_create_lock`** around
  check+create, and links the region only after the puzzle exists.
- **`!canvas` links, and drawing stays web-only** — recorded as a deliberate parity exception in
  a new Canvas section of `docs/commands.md`.

## THE OUTSTANDING WORK — verification, not code

Browser verification is IN PROGRESS on `puzzbot.com` (the Plesk `blissful-bot` test subscription,
AlmaLinux VPS — confirmed the same box as [[plesk-deploy-needs-nonchroot-shell]]'s test
subscription, distinct from the *live* `quarantinedecrypters.com`/`puzzbot` subscription on the
same host). Two real bugs were found and fixed this session (uncommitted — see below):

1. **Upload → "Preparing image" never cleared, even past the DB showing `tile_status='ready'`.**
   `tile_media()` (`bot/core/media_tiling.py`) updates the row on completion but never notified
   anyone; the `canvases` react-query has no poll and is invalidated only once, at canvas-creation
   time while still `pending`. Fixed: `bot/web/api.py` now wraps the background tiling task in
   `_tile_and_push()`, which calls the existing `_push_board()` (the same WS "board" push region
   mutations already use) once tiling finishes.
2. **Intermittent broken/black tiles under the pan/zoom burst.** `/api/media/<id>/tiles/...`
   calls `_load_visible_hunt()` per request, which hits `bot/web/data.py`'s dashboard connection
   pool — hardcoded `pool_size=3`, and mysql-connector's pool is non-blocking
   (`queue.get(block=False)`), so a burst of ~40 near-simultaneous tile requests reliably exhausted
   it and threw `PoolError` → bare 500s. Confirmed via `discord.log` (NOT `bot.log` — the root
   logger's `FileHandler` in `bot/__init__.py:setup_logger` targets `discord.log`; Hypercorn's own
   access-log lines go to `bot.log`/stdout directly, so this app has two separate log files and a
   500's traceback only ever shows up in the former). Fixed: pool size raised to 10, plus a small
   bounded retry (`_get_connection()`, 5 attempts / 50ms) so a still-larger burst degrades to a
   short wait instead of a hard failure.

Committed as `7bd66e2`. Three more real issues turned up in the same live walkthrough, all fixed
and committed:

3. **`!canvas` printed a stray "isn't part of a hunt" error alongside the real embed, then the
   real embed twice.** NOT a code bug — two separate causes stacked: (a) I had left a bot process
   running on `redhat` since earlier in the session, against the stale local test DB, using the
   SAME Discord bot token as `puzzbot.com` (both log in as `Puzzle Hunt Bot Test#9255` in the same
   test guilds) — stopped it; (b) `puzzbot.com` itself had two `python run.py` processes running
   (an old one left over from an earlier test session) — user killed the extra one. No code change.
4. **Draw-to-create forced a round; should allow an unattached puzzle.** The backend route
   (`create_puzzle_from_region`, `bot/web/actions.py`) already treated a missing `round_id` as
   `target_group=None` — the restriction was frontend-only: `RegionModal.tsx` hard-required
   `roundId` and its Round `<Select>` had no "unassigned" option, unlike the board's own
   `CreateControls.tsx` (`"Unassigned (no meta)"`). Fixed to match that existing pattern
   (`UNASSIGNED` sentinel, `useCreatePuzzleFromRegion` omits `round_id` when unset). Added
   `test_an_omitted_round_creates_an_unattached_puzzle` — no prior test covered this path.
5. **Tiles that should be blank/white rendered as solid black**, in *light* mode too (ruling out
   my first theory of a dark-mode background bleeding through a transparent placeholder). Actual
   cause, confirmed by building a real pyramid and inspecting the bytes: dzsave's own
   `blank.png` — written for any tile it omits as uniform-colour — is a **degenerate single-band,
   near-zero-value placeholder that does not reflect the tile's real colour**, not a transparent or
   white one. `media_tile` was serving it verbatim. Fixed: `bot/core/image_tiler.py` now has
   `blank_tile_bytes()`, a real opaque white tile built once via pyvips and cached in memory;
   `bot/web/api.py`'s tile route serves that instead of reading dzsave's `blank.png` off disk.
   Strengthened `test_missing_tile_serves_a_white_tile` to decode the actual pixel (the old
   status/content-type-only assertions would not have caught this — dzsave's blank.png is also a
   200 `image/png`) — proved load-bearing by mutation (near-black constant → red, for the right
   assertion; reverted).
6. **Requested: ctrl-scroll (and trackpad pinch) to zoom, plus real multi-touch pinch on mobile.**
   Added to `CanvasView.tsx`: a raw (non-passive) `wheel` listener anchored on the cursor —
   React's synthetic `onWheel` is passive, so `preventDefault()` silently no-ops there and the
   browser's own page-zoom fires underneath — plus Pointer-Events-based two-finger pinch (anchored
   on the live midpoint, so a simultaneous two-finger pan works too). `zoomBy`/the toolbar buttons
   now go through the same anchored `zoomAt()` helper (anchor = viewport centre for those).

All four verified: `ruff`/`basedpyright` clean, frontend `tsc --noEmit` and `npm run build` clean,
full suite green (1389 passed). Not yet redeployed to puzzbot.com — that's the next step.

1. ~~Drive the real app.~~ Done.
2. ~~Upload, confirm "Preparing image" clears.~~ Confirmed root-caused (bug #1) and fixed.
3. ~~Pan and zoom; check for tile seams/artifacts.~~ Confirmed root-caused (bug #2 pool exhaustion,
   bug #5 black tiles) and fixed. **Still to verify: hairline seams (the +1px fudge) — not yet
   checked.**
4. **Draw a region.** Confirm the box lands where it was drawn *at several zoom levels* — a
   coordinate bug would only show at non-default zoom, which is exactly what the typecheck
   cannot catch. Not yet done.
5. Link it as `locates`, confirm the tint matches the puzzle's status. Re-link as `informs`,
   confirm a row appears in the **Evidence** panel, and that clearing the link removes it. Not yet
   done. **User has also asked for a follow-up feature here — see below.**
6. ~~Draw-to-create a puzzle.~~ Confirmed working; round-forcing bug (#4) found and fixed there.
   Confirm the Discord channel and sheet tab appear — not yet explicitly checked.
7. ~~`!canvas` in a hunt channel.~~ Confirmed working (bug #3, not a code issue).
8. Two browsers on the same canvas: confirm regions appear live without a reload. Not yet done.

## Requested follow-up — DONE, uncommitted

**Informs-region evidence should carry the actual cropped image, not just a text reference.**
User: "When we carve out a part of the image as 'informs' evidence then that section of the image
should be in the evidence table. I would treat it like we uploaded that image for that purpose."

Built exactly to the plan sketched earlier this session:

- `bot/core/image_tiler.py`: new `crop_bytes(src, rect) -> (png_bytes, width, height)`, pure and
  synchronous like `build_pyramid`. Pixel bounds are rounded from the normalised rect and clamped
  (`max(1, min(...))`) — a sub-pixel-wide rect on a huge image can round to a 0-width/height crop,
  which libvips' `crop()` rejects outright; proved load-bearing by mutation (removing the clamp
  reds the edge-case test with "parameter width not set", reverted).
- `bot/core/canvas_shape.py`: new `rect_bounds(payload) -> (x1,y1,x2,y2)`, reusing `parse_shape`
  for validation so there is exactly one place a rect is normalised.
- `bot/core/canvas.py`: `create_region`/`update_region` gained a `media_id=None` parameter,
  forwarded to `_log_informs_evidence`. Default `None` keeps every existing caller/test unchanged.
- `bot/web/actions.py`: new `_crop_region_media(canvas_row, shape)` — looks up the canvas's
  **original** (untiled) media row, runs `crop_bytes` in `run_in_executor`, then
  `save_image_bytes`/`MediaJsonDb.create` (`origin="canvas_crop"`, `tile_status="none"`) to store
  the crop as its own standalone media row. Never raises (mirrors `tile_media`'s contract): a
  crop is a convenience on the evidence row, not a precondition for it, so a failure degrades to
  a text-only evidence row. Wired into `create_region`, `update_region`, and
  `create_puzzle_from_region`, each guarded so a crop only runs when the link is actually
  *changing into* `informs` (checked via the same `existing.puzzle_id != x or existing.role != y`
  comparison `canvas_service.update_region` uses internally) — not on every note/label edit of an
  already-informs region, and not on a half-set link that's about to 400 anyway.
- No frontend change needed: `bot/web/data.py`'s evidence serialisation already builds
  `media_url` from `media_id` generically, and `EvidencePanel.tsx` already renders it — this was
  pure backend wiring of a path that already existed for text-only evidence-with-attachment.

Tests added: `tests/test_image_tiler.py` (crop dimensions, a real gradient-image test that two
different corners decode to different pixels — proves the rect actually selects where it says,
not a fixed corner — edge clamp, sub-pixel clamp), `tests/test_canvas_shape.py` (`rect_bounds`),
`tests/test_canvas_service.py` (`media_id` forwarding), `tests/test_canvas_web.py` (crop wiring
across all three routes, plus direct unit tests of `_crop_region_media` including the
never-raises path). All mutation-proved where the property mattered (the clamp; the
locates/half-set-link "must not crop" guards — confirmed red for the right reason, reverted).

Full suite green (1410 passed), ruff clean, basedpyright 0 errors on every touched file. Not yet
committed — sitting as a working-tree diff across `bot/core/canvas.py`,
`bot/core/canvas_shape.py`, `bot/core/image_tiler.py`, `bot/web/actions.py`, and their four test
files. Not yet verified in a browser (draw an informs region on `puzzbot.com`, confirm the
Evidence panel shows the cropped image).

## OCR — built

Followed up on the 2026-07-26 "is OCR possible" question (see the design doc at
`docs/superpowers/specs/` and the plan in `.ai/plans/` — pytesseract chosen over EasyOCR/cloud
vision per the options weighed earlier: system `tesseract` via EPEL/`dnf` on the AlmaLinux host,
which is viable now that the user confirmed root access). Built across 8 tasks, all committed on
`staging`:

- **`bot/core/ocr.py`** (`f5b7cf1`) — pure `extract_text(image_bytes) -> str`, wraps
  `pytesseract.image_to_string` via PIL. Raises on a corrupt image or tesseract failure by design:
  unlike `image_tiler`/`media_tiling`'s "never raises" helpers, an OCR request exists purely to
  hand the user something back, so callers must surface the failure rather than swallow it.
  `pytesseract` added as a dependency (`9eb1f65`).
- **`POST /api/canvases/<id>/ocr-preview`** (`bot/web/actions.py`, `fc87132`) — crops the canvas's
  original (untiled) media via the existing `crop_bytes`, then OCRs the crop. Same permission bar
  as drawing a region (not organiser-gated). Nothing is persisted; a bad shape is a 400, an OCR
  failure a 500 with `{"error": "ocr_failed"}`.
- **`POST /api/hunts/<uid>/evidence/ocr-preview`** (`bot/web/api.py`, `5b8c16c` +
  size-cap fix `43802e0`) — OCRs an about-to-be-logged clue image (upload or paste) before it's
  logged, same permission bar as logging evidence, same never-persists contract.
- **Frontend wire-ups** (`b13ee5e` hooks, `a449240` RegionModal, `b9070f3` EvidencePanel) — both
  `RegionModal.tsx` and `EvidencePanel.tsx` gained an "Extract text" button that calls the
  respective preview route and, on success, opens a confirm dialog to accept the OCR'd text into
  the note/clue field rather than overwriting it silently.

Tests: `tests/test_ocr.py` (3), `TestCanvasOcrPreview` in `tests/test_canvas_web.py` (6),
`TestEvidenceOcrPreview` in `tests/test_media_web.py` (8, one more than originally planned —
added during review). Full suite green, ruff/basedpyright clean on every touched backend file,
frontend `tsc --noEmit`/`npm run build` clean.

**Not done: manual browser verification.** No subagent across any of the 8 tasks has clicked
either "Extract text" button in a live browser — both routes and both frontend wire-ups are
verified only by unit test and typecheck. This needs a human or a live browser session against
a real puzzle-sheet crop, the same gap the canvas/region work above already carries.

## Known gaps and risks

- **No frontend test runner exists** (`package.json` has no vitest/jest). The whole viewer is
  typecheck-and-inspection only: `npm run build` cannot catch an inverted zoom, a wrong tile
  URL, or a region drawn in the wrong place.
- **Not deployed.** `pyvips[binary]` installing without root on the Plesk host is inference from
  the wheel bundling libvips, not an observed deploy.
- ~360 MB peak RSS per tiling job, with no cross-upload concurrency cap.
- `MAX_DIRECT_PIXELS = 16_000_000` is a judgement call about mobile Safari, not a measurement.

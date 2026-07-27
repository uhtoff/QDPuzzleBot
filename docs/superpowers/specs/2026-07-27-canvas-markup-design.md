# Canvas markup — design

Date: 2026-07-27
Status: designed, not started
Expands: the "Freehand/line/text-box annotations" bullet in
`2026-07-26-canvas-image-evidence-design.md#future-enhancements-requested-2026-07-27-not-started`

## Goal

A coloured-pen layer — freehand strokes, straight lines and arrows, a highlighter, and text
boxes — drawn on top of an image and stored with it.

The motivating use is **not** marking up the whole hunt sheet. It is marking up a section
already identified as a puzzle, and marking up an evidence image.

Two features, and the second is what makes the first reachable:

1. **Markup** on any extracted image.
2. **Puzzle images** — a puzzle carries an ordered set of images, shown in `PuzzleDrawer`,
   populated from its `locates` regions' crops and by direct upload. This is where you open a
   puzzle's section to mark it up, rather than hunting for its box on a 14400px sheet.

## Scope: extracted images only

Markup applies to **already-extracted images** — images that are their own `media` row:

- an **evidence image** (uploaded, pasted, or an informs-region's crop);
- a **`locates` region's crop** — "this section *is* a puzzle", extracted as its own image.

The **big canvas is out of scope**. It keeps its tile pyramid, its regions and its overlay,
and gains no ink.

This is the decision that makes the frontend ordinary. An extracted crop is a normal image, so
every markup surface is a plain `<img>` with an SVG overlay on top — no tile pyramid, no level
selection, no touch-gesture state machine, and **no change to `CanvasView.tsx` at all**. (Zoom
and pan still exist, as a CSS transform; see the frontend section.) An earlier
draft of this design put markup on the canvas too, which forced extracting `CanvasView`'s
pan/zoom/pinch/tile machinery into a reusable viewer: the largest, riskiest and least
verifiable piece of the whole feature, in code that is inspection-verified only. Restricting
to extracted images deletes that stage outright.

Canvas-wide markup remains possible later, with the storage model already proven; it would
need the viewer extraction at that point.

## What markup is not

Markup is **purely visual**. A mark is never linked to a puzzle, never carries a `locates` or
`informs` role, never spawns an `evidence_items` row, is never cropped, and is never OCR'd.
A region is a first-class linked object; ink is not.

**A `locates` region does not create evidence.** An informs region spawns an `evidence_items`
row because it is a clue *for* a puzzle; a locates region says the section *is* that puzzle,
which is not a clue about anything. The region/evidence coupling in `canvas.py`
(`_log_informs_evidence`, its drop-and-recreate on role change) and the `evidence_items`
schema are **unchanged**. The puzzle's own picture belongs to the puzzle, not to the evidence
table.

`canvas.py` is not untouched, though: it gains the region ↔ `puzzle_images` sync described
below, alongside the evidence one. An earlier draft of this spec claimed the module was
untouched; that was true only while locates regions produced nothing.

## Data model

Markup is keyed on **`media_id`**, in that media's own normalised 0–1 coordinate space — the
same coordinate choice regions use, so it survives any change of resolution.

Keying on media rather than on a region or an evidence row means both surfaces share one table
and one implementation: an evidence image and a region's crop are both just `media`.

### New table

```sql
CREATE TABLE `media_markup` (
  `id`         INT(11)     NOT NULL AUTO_INCREMENT,
  `media_id`   INT(11)     NOT NULL,
  `kind`       VARCHAR(16) NOT NULL,   -- 'pen' | 'line' | 'text'
  `shape`      TEXT        NOT NULL,   -- JSON, all coordinates 0-1
  `style`      TEXT        NOT NULL,   -- JSON: colour, width, opacity, arrow
  `text`       TEXT        DEFAULT NULL,
  `created_at` DATETIME    NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_media_markup_media` (`media_id`),
  CONSTRAINT `MediaMarkupMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
);
```

CASCADE, not SET NULL: ink without its image is meaningless. Deleting the media — or the hunt,
which cascades to media — takes the markup with it, so there is no separate cleanup path.

`media.hunt_id` already exists (030), so a markup row inherits its hunt without a second column.

**Highlighter is a style preset**, not a `kind` — a fat, low-opacity pen. **Arrow is a style
flag on `line`**, not a fourth kind — the geometry is identical. Nothing server-side
distinguishes either.

## Puzzle images

A puzzle carries an **ordered set of images**, from two sources:

- a **`locates` region's crop** — a section of a canvas that is this puzzle;
- a **direct upload** — a photo or screenshot that came from no canvas at all.

Because the two sources mix in one ordered list, the list is **materialised**, not derived by
querying regions: you cannot order a list where half the entries exist only as a query result.

```sql
CREATE TABLE `puzzle_images` (
  `id`               INT(11)  NOT NULL AUTO_INCREMENT,
  `puzzle_id`        INT(11)  NOT NULL,
  `media_id`         INT(11)  NOT NULL,
  -- Set when this image came from a locates region; NULL for a direct upload.
  `source_region_id` INT(11)  DEFAULT NULL,
  `position`         INT(11)  NOT NULL DEFAULT 0,
  `created_at`       DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_puzzle_images_region` (`source_region_id`),
  KEY `idx_puzzle_images_puzzle` (`puzzle_id`),
  CONSTRAINT `PuzzleImagePuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `PuzzleImageMediaID` FOREIGN KEY (`media_id`)
      REFERENCES `media` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `PuzzleImageRegionID` FOREIGN KEY (`source_region_id`)
      REFERENCES `canvas_regions` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
);
```

- **`source_region_id` CASCADE**, matching `evidence_items.source_region_id` (033): a
  section image is created *by* the link and is meaningless without the region. A directly
  uploaded image has `source_region_id` NULL and so is never touched by any region change.
- **`UNIQUE` on `source_region_id`** — one region yields at most one image row, which is what
  makes the sync idempotent. (MySQL allows many NULLs in a unique index, so direct uploads are
  unaffected.)
- **`media_id` CASCADE** — a row pointing at a deleted image has nothing to show.
- **`position`** orders the list; reorder is a single route taking the ordered ids.

### The region ↔ puzzle_images sync, in `canvas.py`

Mirroring the evidence coupling that already lives there, and for the same reason — one module
owning both sides so they cannot drift:

- Linking a region as **`locates`** creates the crop and a `puzzle_images` row at the end of
  that puzzle's list.
- **Relinking** to another puzzle moves the row (or drops and recreates it, matching however
  `_log_informs_evidence` handles the same case).
- Changing the role to **`informs`**, or clearing the link, deletes the row — the FK enforces
  the region-deleted case anyway.
- An **unchanged** link does nothing, exactly as the evidence path already does: churning the
  row on a note or label edit would lose its place in the ordering.

### Adding `canvas_regions.crop_media_id`

`ALTER TABLE canvas_regions ADD COLUMN crop_media_id INT(11) DEFAULT NULL` with an FK to
`media` `ON DELETE SET NULL` — a region survives losing its crop.

This is load-bearing for more than convenience. The "don't regenerate a crop that has ink"
guard below needs a way to *find* a region's crop, and today the only record of one is the
`media_id` on an informs region's evidence row — a locates region would have had no route back
to its own crop at all.

## Backend work: a crop for `locates` regions

Only **informs** regions get a crop today; `_crop_region_media` (`bot/web/actions.py`) is
deliberately guarded so a `locates` link produces none. Marking up "this section is a puzzle"
needs one, so the guard extends to `locates` as well.

The pieces exist: `crop_bytes` is role-agnostic and `_crop_region_media` already stores a
standalone `media` row (`origin="canvas_crop"`). It keeps the same never-raises contract — a
failed crop degrades to a region with no markup surface, not a failed request.

**A crop is a snapshot, so it must not be silently regenerated under existing ink.** A region's
geometry cannot be edited from the UI today — `CanvasView` has no resize or move handles, and
`RegionModal` sends back the region's existing shape unchanged — but the route *would* accept
it: `PATCH .../regions/<id>` (`actions.py` ~2352) passes `shape` through to
`canvas.update_region`, which re-validates and re-stores it. So this is a latent path with no
UI in front of it.

The guard is therefore small and pre-emptive rather than a feature: **when a region's crop
would be regenerated, skip it if that crop already has markup rows.** Nothing can trigger it
today; if drag handles ever appear, the ink is already protected instead of silently sliding
off what it was pointing at. Covered by a test, not by UI.

## Validation — `bot/core/markup_shape.py`

A new pure module alongside `canvas_shape.py`, same shape and conventions: no DB, no Quart, no
image; a `MarkupRejected` exception whose message is safe to show a user.

Three parsers sharing a private `_coord` (0–1 float, rejecting `bool` explicitly — it is an
`int` subclass, exactly as in `canvas_shape`):

- `pen` — 2 to `MAX_POINTS` (2000) points. Beyond the cap the payload is **rejected**, not
  truncated: a silently shortened stroke is a wrong stroke, and an uncapped `TEXT` blob is the
  real hazard.
- `line` — exactly 2 points.
- `text` — one point, a non-empty string capped in length, and a normalised font size.

**`canvas_shape.py` is not generalised to serve both.** Its rect rules are load-bearing for
cropping and `rect_bounds`; growing branches there to accommodate ink would put the crop path
one bad refactor away from a bug. Duplicating a six-line `_coord` is the cheaper mistake.

Zero-length strokes and empty text are rejected in the parser, so a stray click cannot persist.

## API

In `bot/web/api.py`:

- `GET    /api/media/<id>/markup`
- `POST   /api/media/<id>/markup`
- `PATCH  /api/media/<id>/markup/<mid>`  — realistically a text box's text, or any mark's style
- `DELETE /api/media/<id>/markup/<mid>`

**Permission**: the bar that already governs `/api/media/<id>` — any member who can see an
unarchived hunt may draw and erase. Same as regions, and deliberately with no per-item author
check: a shared hunt is a shared workspace. No `created_by` column.

**Live sync**: each mutation calls the existing `_push_board()`, the same WS push region
mutations use, so two people marking up one image see each other without a reload.

**Concurrency**: one row per mark means simultaneous drawing touches disjoint rows. (This is
why a single JSON document column was rejected — every save would be a whole-document replace.)

**Undo is client-side**: the session remembers the ids it created and undo issues the
corresponding `DELETE`. No server-side history.

### Puzzle image routes

- `GET    /api/puzzles/<id>/images` — the ordered list (also folded into the puzzle detail
  payload `PuzzleDrawer` already fetches, so the drawer needs no extra request).
- `POST   /api/puzzles/<id>/images` — attach an uploaded media id, appended to the end.
  Uploading goes through the existing media route first, exactly as evidence images do.
- `PATCH  /api/puzzles/<id>/images` — reorder, taking the full ordered list of ids. Whole-list
  rather than per-item so the result cannot be a partial ordering; ids not belonging to the
  puzzle are rejected.
- `DELETE /api/puzzles/<id>/images/<iid>` — detach. For a directly uploaded image this is the
  only way to remove it. For a region-sourced one, removal normally happens by changing the
  region's link; deleting it directly is allowed and does **not** delete the region — it is a
  "stop showing this here" action.

Same permission bar as the rest: any member who can see an unarchived hunt. The media must
belong to the same hunt as the puzzle — the same cross-hunt guard the evidence image-replace
route already carries, and the same one that would otherwise be the way to read past media
visibility.

Mutations push via `_push_board()` like every other board-affecting change.

### Prerequisite: media dimensions on the evidence payload

`get_evidence` (`bot/web/data.py`) serialises a `media_url` but no dimensions. The overlay needs
the image's aspect ratio to lay out, so the attached media's `width` and `height` must be
serialised too (or exposed by a small `GET /api/media/<id>/meta`). Every `media` row already
carries these — `MediaJsonDb.create` records them at upload regardless of tiling.

## Frontend

No change to `CanvasView.tsx`. Two new components, both plain React.

### `MarkupCanvas.tsx`

An `<img>` (`/api/media/<id>`) with an SVG overlay at `viewBox="0 0 1 1"` absolutely positioned
over it, sized by the media's aspect ratio. Pointer coordinates normalise straight off the
image's `getBoundingClientRect()` — which already reflects any CSS transform, so the same
one-line conversion is correct at every zoom level.

Owns:

- the toolbar: pen, line, arrow, highlighter, text, eraser, plus colour and width;
- in-progress stroke capture, dropping points closer than ~0.001 apart and rounding to 4
  decimal places, so a drag commits tens-to-hundreds of points rather than thousands;
- commit on pointer-up; click-to-place text with an inline editor; click-to-erase; undo.

**Text size and stroke width are stored normalised**, so ink scales with the picture — it is
an annotation *on the image*. (The opposite of the region overlay, whose strokes use
`vectorEffect="non-scaling-stroke"` to stay hairline at any canvas zoom.)

### Zoom and pan

Marking up something you cannot read clearly is the failure mode, so the modal zooms:

- **Ctrl/wheel zoom anchored on the cursor**, plus zoom in / out / fit buttons.
- **Drag pans when no tool is selected**; with a tool selected, a drag draws.

Implemented as a CSS transform (`scale` + `translate`) on the image wrapper. There is no
pyramid here, so none of `CanvasView`'s hard parts apply: no level selection, no visible-tile
window, no seam arithmetic. The SVG overlay is inside the transformed wrapper, so existing ink
scales with the image and stays glued to what it points at, with no coordinate work at all.

Two things carry over from `CanvasView` and should be lifted from it rather than rediscovered:

- The wheel listener must be a **raw non-passive DOM listener**, not React's `onWheel` —
  the synthetic one is passive, so `preventDefault()` silently no-ops and the browser's own
  page zoom fires underneath.
- The anchored-zoom arithmetic in `zoomAt` (keep the point under the cursor fixed) is the same
  formula; copy it.

**Touch gestures are out of scope.** Two-finger pinch/pan via Pointer Events is the part of
`CanvasView` that took the most getting right, and re-implementing it here is only worth it if
people mark up on tablets. Listed as an open question.

**Oversized crops** need no special handling now that the modal zooms: a large crop opens
fit-to-view and is zoomed into for detail. It costs decode time and memory, not correctness.

### `MarkupModal.tsx`

A full-screen modal wrapping `MarkupCanvas`, opened from three places:

1. **`EvidencePanel`** — a "Mark up" action on a row with an image.
2. **`PuzzleDrawer`** — clicking one of the puzzle's images.
3. **`RegionModal` / the canvas** — an "Open section" action on a `locates` region, opening its
   crop.

### `PuzzleImages.tsx` — a strip in `PuzzleDrawer`

Thumbnails of the puzzle's ordered images. Each opens `MarkupModal`; a region-sourced one is
marked as such (it points back at its canvas). Plus:

- **Add an image** — file picker or paste, through the existing media upload path, then
  `POST /api/puzzles/<id>/images`.
- **Reorder by dragging**, using `@dnd-kit/core` — already a dependency and already the board's
  drag implementation (`DragBoard.tsx`), so this follows existing practice rather than adding a
  second drag library. Drop commits the whole order in one `PATCH`.
- **Remove**, with the region-sourced caveat above spelled out in the confirm.

## Testing

**Backend, held to the usual bar:**

- `markup_shape`: each kind's happy path; the point cap rejecting rather than truncating; bool
  coordinates; out-of-range coordinates; empty text; zero-length stroke.
- DAO round-trip, including that a mark survives as stored JSON.
- Routes: the permission bar (a non-member is refused), the cross-hunt guard, and the WS push
  firing on each mutation.
- The `locates` crop: created on a locates link, and **not** regenerated when the crop already
  has markup.
- The region ↔ `puzzle_images` sync, held to the same bar as the evidence coupling it mirrors:
  a row on locates link, moved on relink, gone on role change to informs and on clearing the
  link, and **untouched** on a note/label edit that leaves the link alone.
- Reordering: at least **three** images, asserted as a sequence. Two images cannot distinguish
  a real reorder from a reversal or a no-op, and one proves nothing at all.
- A direct upload survives every region change, and a cross-hunt media id is refused.
- Mutation-proved where the property matters — at minimum the point cap, the permission bar,
  the regenerate guard, the unchanged-link no-op, and the reorder sequence, all of which fail
  silently if they regress.

**Frontend: inspection and typecheck only.** There is still no frontend test runner, so
`tsc --noEmit` and `npm run build` cannot catch ink landing in the wrong place or an eraser
that hits the wrong mark. The containment is that existing components gain only *entry points*
— a button in `EvidencePanel`, a strip in `PuzzleDrawer`, an action on a region — while all the
new behaviour lives in new files. In particular `CanvasView` and its verified-by-hand
pan/zoom/tile machinery are not touched, so a markup bug cannot regress the canvas.

## Stages

| # | What | Scale |
|---|------|-------|
| 1 | Migration (`media_markup`, `puzzle_images`, `canvas_regions.crop_media_id`) + `markup_shape.py` + DAOs | small, fully tested |
| 2 | Markup routes + permission + WS push | small, fully tested |
| 3 | `locates` crops, the regenerate-under-ink guard, the region ↔ `puzzle_images` sync in `canvas.py` | medium, fully tested |
| 4 | Puzzle image routes (list/attach/reorder/detach) + media dimensions on the payloads | small, fully tested |
| 5 | `MarkupCanvas` + `MarkupModal`, wired into `EvidencePanel` and the region | medium, inspection-verified |
| 6 | `PuzzleImages` strip in `PuzzleDrawer`: thumbnails, upload, dnd-kit reorder, remove | medium, inspection-verified |

Stages 1–4 are backend and fully covered; 5 and 6 are the inspection-only frontend, and neither
touches existing frontend code beyond adding an entry point.

## Rejected approaches

- **Markup on the canvas itself (this design's first draft).** Would have required extracting
  `CanvasView`'s pan/zoom/pinch/tile machinery into a reusable `ImageViewer`, plus an
  active-tool protocol so a drag could mean pan *or* draw. Medium-sized, highest-risk, and
  unverifiable by any automated check — in the exact code that took several live bugs to get
  right. Deferred, not ruled out.
- **A JSON document column on `hunt_canvases`.** One route and no new table, but every save
  replaces the whole document, so two people drawing simultaneously clobber each other. Also
  unbounded growth in one row, with no way to delete a single stroke server-side.
- **A `kind` column on `canvas_regions`.** Reuses the table and routes, but every existing
  consumer — the puzzle-link machinery, evidence coupling, `resolve_region_names`, the
  highlight picker, the evidence board's Source filter — would need a `kind='region'` filter
  added, and missing one is a silent bug (a scribble appearing as an evidence source). Markup
  has none of a region's columns and a region has none of markup's.
- **Linkable markup** (a stroke carrying `locates`/`informs`). Would reuse the region
  machinery, but arbitrary geometry has to answer "what area do I crop?", and it would fill the
  evidence table with scribbles.
- **An evidence row for a locates region.** The obvious way to make a puzzle's section
  reachable from the puzzle side, and it would have reused machinery that already exists — but
  a locates region is not a clue, and every region on a sheet would have produced a row.
  Puzzle images do the same job without overloading what evidence means.
- **A single `puzzles.image_media_id` column.** Cannot express a puzzle whose sections are
  scattered across the sheet in several regions — which is precisely what highlight-by-puzzle
  exists for — and has nowhere to put an ordering.
- **Deriving a puzzle's images from its regions by query**, with no table. Works right up until
  you want a directly-uploaded image or a custom order, neither of which a query over
  `canvas_regions` can hold.
- **Per-author layers with visibility toggles.** Powerful on a crowded image, but it needs a
  `created_by` column, author filtering and a layer UI, none of which regions needed. Revisit
  if a real image gets crowded.
- **Screen-pixel text sizing.** A text box is ink on the picture; sizing it in screen pixels
  decouples it from what it annotates.

## Open questions

- **Ink on the canvas's own overlay.** Out of scope here by choice; revisit once the extracted
  surfaces are in use and it is clear whether whole-sheet ink is actually wanted.
- **Does markup need to appear in an exported or Sheets view?** Today nothing renders these
  images outside the dashboard.
- **Eraser granularity.** The design erases a whole mark on click. Partial-stroke erasing would
  need stroke splitting and is not proposed.
- **Touch gestures in the markup modal.** Wheel zoom and drag-pan cover desktop. Pinch-zoom
  would need a second implementation of `CanvasView`'s Pointer-Events state machine; deferred
  until someone actually marks up on a tablet.

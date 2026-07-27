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

`canvas_regions`, `bot/core/canvas.py` and the whole region/evidence coupling (role changes
creating and dropping evidence rows, `resolve_region_names`, the Source filter) are untouched.
A region is a first-class linked object; ink is not.

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

A full-screen modal wrapping `MarkupCanvas`, opened from two places:

1. **`EvidencePanel`** — a "Mark up" action on a row with an image.
2. **`RegionModal` / the canvas** — an "Open section" action on a `locates` region, opening its
   crop.

## Testing

**Backend, held to the usual bar:**

- `markup_shape`: each kind's happy path; the point cap rejecting rather than truncating; bool
  coordinates; out-of-range coordinates; empty text; zero-length stroke.
- DAO round-trip, including that a mark survives as stored JSON.
- Routes: the permission bar (a non-member is refused), the cross-hunt guard, and the WS push
  firing on each mutation.
- The `locates` crop: created on a locates link, and **not** regenerated when the crop already
  has markup.
- Mutation-proved where the property matters — at minimum the point cap, the permission bar,
  and the regenerate guard, all of which fail silently if they regress.

**Frontend: inspection and typecheck only.** There is still no frontend test runner, so
`tsc --noEmit` and `npm run build` cannot catch ink landing in the wrong place or an eraser
that hits the wrong mark. The containment here is that no existing frontend code changes:
`CanvasView` and its verified-by-hand pan/zoom/tile machinery are not touched, so a markup bug
cannot regress the canvas.

## Stages

| # | What | Scale |
|---|------|-------|
| 1 | Migration + `markup_shape.py` + DAO | small, fully tested |
| 2 | Routes + permission + WS push | small, fully tested |
| 3 | `locates` crops + the regenerate-under-ink guard + media dimensions on the evidence payload | small, fully tested |
| 4 | `MarkupCanvas` + `MarkupModal`, wired into `EvidencePanel` and the region | medium, inspection-verified |

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

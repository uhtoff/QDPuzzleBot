# Canvas markup — design

Date: 2026-07-27
Status: designed, not started
Supersedes: the "Freehand/line/text-box annotations" bullet in
`2026-07-26-canvas-image-evidence-design.md#future-enhancements-requested-2026-07-27-not-started`

## Goal

A coloured-pen layer — freehand strokes, straight lines and arrows, a highlighter, and text
boxes — drawn on top of an image and stored with it.

The motivating use is **not** marking up the whole hunt sheet. It is marking up a section
already identified as a puzzle (a `locates` region), and marking up an evidence image. The
canvas-wide case comes along for free because all three are the same image with a different
frame around it.

## What markup is not

Markup is **purely visual**. A mark is never linked to a puzzle, never carries a `locates` or
`informs` role, never spawns an `evidence_items` row, is never cropped, and is never OCR'd.

This is the decision the rest of the design rests on: `canvas_regions`, `bot/core/canvas.py`
and the whole region/evidence coupling (role changes creating and dropping evidence rows,
`_crop_region_media`, `resolve_region_names`, the Source filter) are **untouched** by this
feature. A region is a first-class linked object; ink is not.

## Data model

Markup is keyed on **`media_id`**, in that media's own normalised 0–1 coordinate space —
the same coordinate choice regions use, for the same reason (it survives re-tiling and any
change of resolution).

Keying on media rather than on a canvas or a region is what collapses three surfaces into one
table:

- **Canvas markup** → rows on the canvas's original media.
- **Markup drawn while zoomed into a locates-region** → *the same rows*. A region is a window
  onto that media, not a document of its own, so ink drawn inside its rect is already at
  canvas coordinates. Nothing to reconcile, nothing to migrate if a region moves.
- **Evidence-image markup** → rows on that evidence media row.

Accepted consequence, named deliberately: an **informs** region's crop is a *separate* media
row (`origin="canvas_crop"`), so ink on the canvas does not appear on the crop in the evidence
table, and vice versa. They are genuinely different images.

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

`media.hunt_id` already exists (030), so a markup row inherits the hunt it belongs to without a
second column.

**Highlighter is a style preset**, not a `kind` — a fat, low-opacity pen. Nothing server-side
distinguishes it.

**Arrow is a style flag on `line`**, not a fourth kind — the geometry is identical.

## Validation — `bot/core/markup_shape.py`

A new pure module alongside `canvas_shape.py`, same shape and same conventions: no DB, no
Quart, no image; a `MarkupRejected` exception whose message is safe to show a user.

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

Mirrors the existing region routes, in `bot/web/api.py`:

- `GET    /api/media/<id>/markup`
- `POST   /api/media/<id>/markup`
- `PATCH  /api/media/<id>/markup/<mid>`  — realistically a text box's text, or any mark's style
- `DELETE /api/media/<id>/markup/<mid>`

**Permission**: the bar that already governs `/api/media/<id>` — any member who can see an
unarchived hunt may draw and erase. Same as regions, and deliberately with no per-item author
check: a shared hunt sheet is a shared workspace. No `created_by` column.

**Live sync**: each mutation calls the existing `_push_board()`, the same WS push region
mutations use, so two people marking up one sheet see each other without a reload.

**Concurrency**: one row per mark means simultaneous drawing touches disjoint rows. (This is
why a single JSON document column on `hunt_canvases` was rejected — every save would be a
whole-document replace, and two people drawing at once is the normal case.)

**Undo is client-side**: the session remembers the ids it created and undo issues the
corresponding `DELETE`. No server-side history.

### Prerequisite: media dimensions on the evidence payload

`get_evidence` (`bot/web/data.py`) serialises a `media_url` but no dimensions. A generic viewer
cannot lay out an image it cannot size, so the attached media's `width`, `height`,
`tile_status` and `tile_levels` must be serialised too (or exposed by a small
`GET /api/media/<id>/meta`). Every `media` row already carries these — `MediaJsonDb.create`
records `width`/`height` at upload regardless of whether the image is later tiled.

## Frontend

### `ImageViewer.tsx` — extracted from `CanvasView`

`CanvasView.tsx` is 511 lines and splits roughly 60/40 into media-generic machinery and
region-specific behaviour. The generic part moves out unchanged:

viewport `ResizeObserver`; `fit`; `scale`/`offset`; `zoomAt`; the non-passive `wheel` listener
(React's synthetic `onWheel` is passive, so `preventDefault` no-ops there); the pointer/pinch
state machine; pyramid level selection; the visible-tile window; the integer-grid tile snapping
that fixed the seams; and the untiled `<img>` fallback for `tile_status` `none`/`failed`.

Props: `mediaId, width, height, tileLevels, tileStatus, initialRect?`. It renders the tiles and
then `children` as a render-prop given `{ dispW, dispH, offset, scale, toNorm, toScreen }`, so
any overlay can position itself and convert in both directions.

Staying in `CanvasView`: `tintFor` and the legend, `paintOrder` with its hover-flicker
exception, the highlight picker, region hit-testing, draw-a-region, and the `pending` loader.

**The one thing the extraction must redesign rather than move: drag ownership.** Today the
wrapper decides pan-vs-draw from a single `drawing` boolean it owns. With several
drag-consuming tools, `ImageViewer` instead takes an active-tool prop: the viewer always owns
wheel and pinch, and delegates single-pointer drags to the active tool when one is set.
Getting this wrong is how you end up unable to pan while the pen is selected.

### `MarkupLayer.tsx`

A second SVG at `viewBox="0 0 1 1"` inside the render-prop — beneath the region overlay on the
canvas, alone on the other two surfaces. Owns:

- the toolbar: pen, line, arrow, highlighter, text, eraser, plus colour and width;
- in-progress stroke capture, dropping points closer than ~0.001 apart and rounding to 4
  decimal places, so a drag commits tens-to-hundreds of points rather than thousands;
- commit on pointer-up; click-to-place text with an inline editor; click-to-erase; undo.

**Text size and stroke width are stored normalised**, so ink scales with the picture — it is
an annotation *on the image*. This is the opposite of the region overlay, whose strokes use
`vectorEffect="non-scaling-stroke"` to stay hairline at any zoom; ink must not.

### Three surfaces, one implementation

1. **Canvas** — `CanvasView` as today, plus `MarkupLayer`.
2. **Region focus** — a `locates` region gains an "Open section" action: the same `ImageViewer`
   on the canvas's media with `initialRect` set to the region's bounds. Same rows, same ink,
   just framed. Once the viewer is generic this is a camera argument, not a feature.
3. **Evidence image** — a full-screen lightbox in `EvidencePanel` wrapping `ImageViewer` on the
   evidence media plus `MarkupLayer`. Row thumbnails are unchanged.

## Testing

**Backend, held to the usual bar:**

- `markup_shape`: each kind's happy path; the point cap rejecting rather than truncating;
  bool coordinates; out-of-range coordinates; empty text; zero-length stroke.
- DAO round-trip, including that a mark survives as stored JSON.
- Routes: the permission bar (a non-member is refused), the cross-hunt guard, and the WS push
  firing on each mutation.
- Mutation-proved where the property matters — at minimum the point cap and the permission
  bar, both of which fail silently if they regress.

**Frontend: inspection and typecheck only.** There is still no frontend test runner, so
`tsc --noEmit` and `npm run build` cannot catch an inverted zoom, ink landing in the wrong
place, or a pan that stops working when a tool is active.

**Containment, chosen deliberately:** the `ImageViewer` extraction lands as its **own commit
that changes no behaviour**, and the outstanding B2b browser checklist is re-run against it
before any markup code lands. If the canvas still pans, zooms, pinches and draws regions
identically, the extraction was clean.

(The alternative — standing up vitest first and pinning the coordinate maths as pure-function
tests — was weighed and deferred. It is the first real safety net the frontend would have and
would have caught several of this week's bugs, so it remains the right thing to do eventually;
it is just not a prerequisite for this feature.)

## Stages

| # | What | Scale |
|---|------|-------|
| 1 | Migration + `markup_shape.py` + DAO | small, fully tested |
| 2 | Routes + permission + WS push | small, fully tested |
| 3 | `ImageViewer` extraction — no behaviour change, own commit, browser pass | medium, highest risk |
| 4 | `MarkupLayer` + canvas surface | medium |
| 5 | Region focus **and** evidence lightbox, together | small, given 3 and 4 |

Stage 5 does both surfaces in one pass: they are small once the viewer and layer exist, and
doing them together avoids a second round of the same review.

## Rejected approaches

- **A JSON document column on `hunt_canvases`.** One route and no new table, but every save
  replaces the whole document, so two people drawing simultaneously clobber each other — the
  normal case on a shared sheet. Also unbounded growth in one row, with no way to delete a
  single stroke server-side.
- **A `kind` column on `canvas_regions`.** Reuses the table and routes, but every existing
  consumer — the puzzle-link machinery, evidence coupling, `resolve_region_names`, the
  highlight picker, the evidence board's Source filter — would need a `kind='region'` filter
  added, and missing one is a silent bug (a scribble appearing as an evidence source). Markup
  has none of a region's columns and a region has none of markup's.
- **Linkable markup** (a stroke carrying `locates`/`informs`). Would reuse the region
  machinery, but arbitrary geometry has to answer "what area do I crop?", and it would fill the
  evidence table with scribbles.
- **Per-author layers with visibility toggles.** Powerful on a crowded sheet, but it needs a
  `created_by` column, author filtering and a layer UI, none of which regions needed. Revisit
  if a real sheet gets crowded.
- **Screen-pixel text sizing.** Stays legible at every zoom, but a text box is ink on the
  picture; at low zoom, constant-size labels overlap into noise.

## Open questions

- **Does markup need to appear in an exported or Sheets view?** Today nothing renders images
  outside the dashboard, so this is only a question if evidence images ever get exported.
- **Eraser granularity.** The design erases a whole mark on click. Partial-stroke erasing
  would need stroke splitting and is not proposed.

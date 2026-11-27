# Image overlay tool — design

Date: 2026-11-27. Status: design, not yet planned.

## Intent

Hunts often need one image laid over another and pushed around until something lines up:
stickers placed on a map, a cut-out shape fitted over a logo. Today that means GIMP, which
is too fiddly for a two-minute experiment, and no online tool found so far lets you just
drag, scale, rotate and fade layers freely. Add a scratch tool to the dashboard for this:
pick a background, drop other images on it, play with them, and — if the result is worth
keeping — save it as a puzzle image so the team has it immediately.

It is a single-person scratch pad, not a shared editor.

### The two driving examples

1. **Stickers on a map (MIT 2026).** A fixed map of Westeros as the background; about eight
   small transparent-PNG stickers, each with a ⊕ crosshair at its centre and a north marker
   at its top. Each sticker is scaled to the map, rotated, and its crosshair placed on a
   town. Afterwards the team drew arrows between them — **that is done in the existing
   mark-up editor on the saved result, not in this tool** (user, 2026-11-27).
2. **Missing-image shapes over logos (Puzzle Boat 10).** Each given image is a solid white
   shape (triangle, rectangle, ellipse, tilted square) carrying a number and a broken-image
   icon. The background is a logo the solver found online and pasted in. The shape has to
   be made see-through, stretched to the logo's outline and possibly rotated, to read which
   letter the number sits over. The overlay sources were screenshots of a whole page, so a
   layer may need cropping down to just the shape.

## Goals

- Open from a puzzle; start with a background and any number of overlay layers.
- Layers come from the puzzle's existing images, a file picker (several at once), drag and
  drop of files, or **Ctrl+V paste**. Paste is the primary path.
- Per layer: move, uniform scale, independent width/height stretch, rotate, opacity, blend
  mode, crop, flip, reorder, hide, delete.
- Zoom and pan the view for precise placement.
- Result: **Copy** to clipboard (for Discord), and **Save to puzzle**, which adds the
  flattened PNG to the puzzle's images.
- What you see on screen is exactly what gets saved.

## Non-goals

- Live or shared editing.
- Reopening a saved overlay to adjust its layers. Save flattens; the layer arrangement is
  discarded (user picked "flatten only", 2026-11-27). To redo it, start again from the
  originals, which are still on the puzzle.
- Drawing, arrows, text — that is the mark-up editor's job, applied to the saved image.
- Touch / phone use. Mouse and keyboard on a desktop only (user, 2026-11-27). It must not
  break on a phone, but need not be usable there.
- Any server, database or migration change.

## Anti-goals

- A cut-down GIMP. If a task needs more than a minute of learning, the tool has failed its
  purpose. Few controls, obvious ones, keyboard shortcuts for the precise bits.
- A preview that differs from the saved image.

## Constraints and what exists

- Frontend: React 18 + Mantine 7 + TanStack Query, Vite, vitest (`frontend/`).
- **Puzzle images strip**: `frontend/src/components/PuzzleImages.tsx`, rendered in
  `PuzzleDrawer.tsx`. It uploads with `useUploadMedia(uid)` (`POST /api/hunts/{uid}/media`,
  multipart field `file`) then attaches with `useAttachPuzzleImage(puzzleId)`
  (`POST /api/puzzles/{id}/images` with `media_id`). Save reuses exactly these two calls.
- Upload accepts PNG/JPEG/GIF/WebP, sniffed server-side; cap is
  `web.attachment_max_bytes` (default 25 MiB). Large uploads are tiled automatically by
  the existing pipeline — nothing to do here.
- Media is served same-origin at `/api/media/{media_id}` (`PuzzleImage.media_url`), so
  images drawn onto a canvas do not taint it and `toBlob` works.
- Writes are refused on an archived hunt; `PuzzleImages` already has an `editable` flag.
- Clipboard helpers: `imageFromClipboard` and `isTextEntryTarget` in `frontend/src/util.ts`.
- Zoom: `useWheelZoom` in `frontend/src/hooks/useZoomPan.ts` (Ctrl+wheel / trackpad pinch
  zooms about the cursor), shared by `CanvasView` and `MarkupCanvas`.
- Pure geometry lives in small tested modules such as `frontend/src/lib/canvasMath.ts`.
- Full-screen modal pattern: `MarkupModal.tsx` (flex-column body so the canvas can fill it).

## Approaches considered

1. **One HTML canvas drawn by our own code (chosen).** A single `draw(ctx, layers, view)`
   function renders both the screen and the export, so they cannot disagree — this matters
   for blend modes and pixel alignment. No new dependency. Geometry (transforms, handle
   hit-testing, crop) is pure functions with unit tests. Cost: we write the move / scale /
   stretch / rotate handles ourselves.
2. **`react-konva`.** Ready-made transform handles including rotation, and built-in
   export. Rejected: a ~150 KB dependency, export goes through its own pixel-ratio scaling
   (another place for preview and output to drift), and its state is harder to unit test.
   Its main advantage — touch gestures — is a non-goal.
3. **DOM elements with CSS transforms, redrawn to canvas on save.** Easiest dragging, but
   CSS `mix-blend-mode` and canvas compositing are two engines that can disagree, and every
   effect is implemented twice. Rejected.

## Design

### Where it lives

- An **Overlay** button in the puzzle images strip, beside the add-image button, shown when
  `editable`. It opens a full-screen modal (`OverlayModal`, same shape as `MarkupModal`).
- If the puzzle has images, the modal opens on a picker: choose one as background and tick
  any as layers. An image that has mark-up (arrows, notes drawn in the mark-up editor) is
  flagged in the picker: "Has mark-up — not included". Mark-up is stored separately from
  the image file and is **not** brought into the overlay (user, 2026-11-27); the expected
  order is overlay first, then mark-up on the saved result. The puzzle images payload has no
  mark-up count today; fetching `useMarkup(mediaId)` per image in the picker is fine at
  hunt scale (a handful of images). Adding a count to the payload instead is the
  builder's call. "Skip" opens empty. In the empty state the canvas says
  "Paste, drop or choose an image for the background".
- **The first image added becomes the background**; every later one is a layer. Any layer
  can be promoted with "Use as background", which demotes the old background to a layer.
  *(Design call, 2026-11-27.)* Promotion keeps the layer's **crop and flip** and drops its
  scale, stretch and rotation: the new background is the cropped area at its natural
  resolution, which sets the output scale. The demoted background and all other layers
  are re-projected into the new background's pixels so they stay where they appear on
  screen. This is expected to be rare (mainly "I pasted the logo second"), so it does not
  need to be cleverer than that.

### The background

- Fixed: it is the coordinate frame. It cannot be moved, scaled or rotated; it can be
  hidden and its opacity changed (useful to see a layer alone).
- It sets the output's **scale** (one output pixel = one background pixel) but not its
  extent: the saved image **grows to fit** (user, 2026-11-27). See Output.
- **Working frame** (user, 2026-11-27): the background is shown inside a blank margin of
  25% of its width/height on every side, drawn as a visibly different, lightly shaded area,
  so layers can be dragged off the background, parked, and brought back. "Fit" fits the
  background *plus* this frame. Layers may also be dragged beyond the frame; the frame is a
  visual guide, not a boundary.
- *(Design call, 2026-11-27.)* Browsers cap canvas size (Chrome ~16384 px a side; Safari
  far lower) and the server accepts images up to 300 Mpx. If a background exceeds 16384 px
  on either side or 100 Mpx in area, it is scaled down on load to fit those limits and the
  tool says so ("Background reduced to W×H for editing"); output scale then follows the
  reduced background. This is the one permitted downscale, and it is never silent. No
  tiled export.

### Layers

Each layer holds: source image, position of its centre (background pixels), scaleX,
scaleY, rotation (degrees), opacity (0–100%), blend mode, crop rectangle (in the layer's
own pixels), flipX, visible, and a short label (file name or "Pasted 3").

New layers are placed centred on the current view and scaled so their longer side is about
a third of the background's shorter side — but never enlarged beyond 100% if smaller.
(Builder may tune the fraction.)

**Transforms are about the layer's centre** — of the cropped area, once cropped. This is
what keeps a sticker's centre crosshair on its town while you rotate or resize it.

### Selecting and handles

- Click selects the topmost visible layer whose **non-transparent** pixel is under the
  pointer, so clicking through a sticker's transparent corner reaches the one below.
  Clicking the background or empty space deselects. A layer can also be selected in the
  layer list; Tab / Shift+Tab cycle selection.
- The selected layer shows a box with:
  - **Corner handles** — uniform scale about the centre.
  - **Side handles** — stretch width or height alone, about the centre.
  - **Rotate handle** above the top edge — rotate about the centre; **Shift** snaps to 15°.
  - Dragging anywhere inside the box moves it.
- Handles are drawn at a constant screen size whatever the zoom.

### Keyboard (when focus is not in a text field)

| Key | Action |
|---|---|
| Arrows | nudge 1 screen pixel; Shift+arrows 10 |
| + / − | scale up / down ~2% |
| [ / ] | rotate −1° / +1°; with Shift, ±15° |
| 0–9 | opacity 10%…90%, 0 = 100% |
| Delete / Backspace | remove layer |
| H | hide / show |
| Ctrl+Z / Ctrl+Shift+Z | undo / redo (session only) |
| Ctrl+C | Copy result (when nothing in the page has a text selection) |
| Esc | leave crop mode, else deselect |

Nudging in **screen** pixels (not background pixels) makes the arrow keys finer as you zoom
in, which is what precision needs.

### View

- Ctrl+wheel / trackpad pinch zooms about the cursor (reuse `useWheelZoom`). Plain wheel
  scrolls the view vertically, Shift+wheel horizontally.
- Space+drag or middle-drag pans. A "Fit" button and the `F` key fit the background to the
  window.
- A checkerboard behind the background so transparency is visible. It is drawn **behind
  the canvas element** (CSS), never into it — otherwise Multiply and Difference would
  blend with the checkerboard on screen but not in the export.

### Side panel (selected layer)

- Opacity slider with the number shown.
- Blend mode: **Normal**, **Multiply** ("hide white" — white becomes invisible, dark marks
  stay), **Screen** ("hide black"), **Difference** (aligned edges go black). One-click
  segmented control, not a dropdown. These map to canvas `globalCompositeOperation`
  `source-over`, `multiply`, `screen`, `difference`.
- Numeric fields for X, Y, width %, height %, rotation — for exact entry. A lock toggle
  ties width and height % together (on by default).
- Buttons: Flip horizontal, Crop, Reset transform, Use as background, **Match size to
  selected** (see below), Delete.

**Match size**: with a layer selected, "Copy size" remembers its scaleX/scaleY; "Paste
size" applies them to the selected layer. That is how all the map stickers get one scale.
(Builder may instead do multi-select with "apply to all selected"; the requirement is
sizing many layers identically in a few clicks.)

### Layer list

Thumbnails, top of the list = top of the stack. Drag to reorder (dnd-kit is already a
dependency), eye icon to hide, click to select. The background sits at the bottom, marked
and not draggable.

**Solo (user, 2026-11-27):** Alt+click an eye shows only that layer (or only the
background) and hides everything else. Alt+clicking the same eye again restores exactly the
visibility from before the solo. Any other show/hide while soloed simply keeps the current
state and drops the remembered one. Expected to be used rarely; keep it small.

### Crop

The Crop button puts the selected layer into crop mode: it is shown un-rotated at its
current scale with the full image dimmed and the crop rectangle bright, adjustable by its
edges and corners. Enter or the button again applies; Esc cancels. Crop is non-destructive
within the session (the original image is kept; "Reset crop" restores it). Rectangle only.

### Output

**Bounds — grow to fit (user, 2026-11-27).** The exported image is the smallest
axis-aligned rectangle, in background pixels, containing the background and the rotated,
cropped bounds of every **visible** layer, rounded out to whole pixels. If every layer sits
inside the background, that is exactly the background's size. Area outside the background
is transparent. Hidden layers do not count, so a layer parked far off to the side should be
hidden to keep it out of the result; the tool should make this obvious by drawing the
export bounds as a dashed outline at all times.

If the grown bounds exceed the canvas limits above (16384 px a side or 100 Mpx), Copy /
Save / Download refuse with a message naming the layer(s) furthest out, rather than
shrinking the result.

- **Copy** renders those bounds at background scale and writes a PNG to the clipboard
  (`navigator.clipboard.write` with a `ClipboardItem`). If the browser refuses, fall back
  to a download and say so.
- **Save to puzzle** renders the same PNG, uploads it as `overlay.png` via
  `useUploadMedia`, attaches it via `useAttachPuzzleImage`, notifies success, and leaves
  the tool open (the user may want to save a variant). The new image then appears in the
  strip and can be opened in the mark-up editor for arrows.
- **Download** as a third, minor button.
- Hidden layers are left out of the export. The background's own opacity and visibility
  do apply (a hidden background exports as transparent).
- Very large backgrounds: if the rendered PNG exceeds the upload cap, say so and offer
  Download instead. No silent downscaling.

### Paste handling

`PuzzleImages` already listens for `paste` on the whole document and uploads any pasted
image to the puzzle. While the overlay is open its own paste handler must run first (a
capture-phase listener) and `preventDefault` + stop propagation, so a pasted layer is not
also added to the puzzle's images. The existing listener already skips
`defaultPrevented` events.

### Closing

Closing with unsaved changes asks for confirmation (nothing is persisted, so closing loses
the arrangement). Uploading layers does **not** add them to the puzzle's images — pasted
or picked layers live only in the browser until the result is saved.

### Archived hunts / not editable

The Overlay button is hidden when `editable` is false, matching the add-image button.
(Copy and Download alone would still be useful for an archived hunt; leaving that out
keeps this simple — builder's call if it is trivial.)

## Related

The mark-up editor gets its own blank margin for drawing outside the image — a separate
change, specified in `2026-11-27-markup-margin-design.md`. Neither depends on the other.

## Testing

- Unit tests (vitest) for the pure geometry: layer-to-screen and screen-to-layer
  transforms with rotation and non-uniform scale, handle hit-testing, scale/stretch/rotate
  about the centre (the centre must not move), crop-adjusted centre, nudge in screen pixels
  at different zooms, initial placement sizing.
- Alpha-aware hit testing: a test with two overlapping layers where the top one is
  transparent at the click point selects the lower one.
- Export: the draw function issues the expected calls for a known two-layer scene (order,
  transform, `globalAlpha`, `globalCompositeOperation`, crop source rectangle) — tested
  against a recording fake context, since jsdom has no real canvas. Pixel-level blend
  results are left to the manual check below.
- A manual check list for the user on their desktop: both driving examples end to end,
  including Save → open in mark-up editor.

## Left to the builder

- Component and file names, how state is held (a reducer is the obvious fit for undo).
- Exact handle sizes, colours, panel layout, default placement fraction.
- Copy/paste size versus multi-select, as above.
- Whether to downsample very large images for on-screen drawing (export must still use
  full resolution).

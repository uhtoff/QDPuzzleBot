# Mark-up editor: blank margin for drawing outside the image — design (short)

Date: 2026-11-27. Status: design, not yet planned. Independent of the overlay tool
(`2026-11-27-image-overlay-design.md`); either can ship first.

## Intent

When marking up an image the user often wants to draw a little outside it: an arrow
coming in from the edge, a note beside the picture rather than over it. Today every mark
is pinned inside the image.

## How it works today

- Marks are stored as normalised image coordinates, 0–1 across and down
  (`MarkupCanvas.tsx` draws them in an SVG with `viewBox="0 0 1 1"` laid exactly over the
  `<img>`; text marks are an HTML layer over the same box).
- The client clamps every pointer position to 0–1 (`normPoint`, `frontend/src/lib/canvasMath.ts`,
  used only by `MarkupCanvas`).
- The server refuses coordinates outside 0–1 (`_coord`, `bot/core/markup_shape.py`).
- Mark-up is never baked into the image file; it is only ever drawn by `MarkupCanvas`.
  So nothing else reads these coordinates.
- `bot/core/canvas_shape.py` has its own `_coord` for canvas regions — **not** part of this
  change; regions stay inside their image.

## Design

- A blank margin of **25% of the image's width / height on each side**, shown as a lightly
  shaded area so it reads as "outside the picture". The image plus margin is what the
  editor fits to the window; zoom and pan work as now.
- Allowed coordinates widen to **−0.25 … 1.25** on both axes, in both places:
  `normPoint` (or a margin-aware variant used by `MarkupCanvas`) and `markup_shape._coord`.
  Name the margin once per side (a shared constant in each language) so client and server
  cannot drift; a test on each side pins the bounds.
- Rendering: the SVG and the text layer cover the image-plus-margin box, with the SVG's
  `viewBox` becoming `-0.25 -0.25 1.5 1.5` so stored coordinates still mean "fraction of
  the image". Existing marks therefore render exactly where they were. No data migration.
- Sizes that are fractions of the image (stroke width, text size, eraser radius) keep
  meaning fractions of the image, not of the framed box.

## Approaches

Only one sensible way: widening the coordinate range keeps every existing mark valid and
needs no schema change. Storing marks relative to the framed box instead would require
rewriting every stored mark, for no gain.

## Non-goals

- Any change to canvas regions.
- A configurable margin size.

## Testing

- Server: a coordinate of −0.25 and 1.25 is accepted; −0.26 and 1.26 are refused.
- Client: `normPoint` (or its replacement) clamps to the new bounds; a point in the margin
  maps to a value below 0 / above 1.
- Manual: existing marked-up images look unchanged; drawing, text and eraser all work in
  the margin.

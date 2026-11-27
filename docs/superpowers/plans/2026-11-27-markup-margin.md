# Mark-up margin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users draw marks in a blank margin around an image in the mark-up editor.

**Architecture:** Mark coordinates stay "fraction of the image"; the allowed range widens
from 0…1 to −0.25…1.25 on the server and the client. The editor keeps its existing
image box (img + SVG + text layer) unchanged and wraps it in a shaded, padded box; the SVG
gets `overflow: visible` so marks outside 0…1 still draw.

**Tech Stack:** Python 3.12 (pytest), React 18 + TypeScript (vitest, node environment — no DOM).

**Spec:** `docs/superpowers/specs/2026-11-27-markup-margin-design.md`

## Global Constraints

- Margin = **0.25** of the image's width/height on each side; coordinates allowed in
  **[−0.25, 1.25]** on both axes. Named once per language.
- Existing marks render exactly where they did. No migration, no data rewrite.
- Canvas regions (`bot/core/canvas_shape.py`) are **not** changed.
- Stroke width, text size and eraser radius stay fractions of the **image**, not the framed box.
- Before push: `pipenv run basedpyright bot/` reports 0 errors (CI gate); `cd frontend && npm run build` passes.

## Review Focus

1. **Existing marks shift.** Opening an already-marked image must show every mark in the
   same place relative to the picture — the inner image box must stay the box `toNorm`
   measures and the SVG/text layer cover. (Manual check, Task 2 step 6.)
2. **Clicking in the margin at the very edge of the frame** must clamp to −0.25/1.25, not
   send 1.26 and get a 400. (Test in Task 2.)
3. **Text marks placed in the left/top margin** (negative percentages) must render there,
   not snap to 0. (Manual check, Task 2.)
4. **Eraser over a mark in the margin** must still hit it (`polylineHitsCircle` takes
   normalised points; negatives must work). (Test in Task 2.)
5. **Image now smaller on screen** because the frame takes room: it must still fit the
   modal with the margin fully visible at default zoom. (Manual check, Task 2.)

---

### Task 1: Server accepts coordinates in the margin

**Files:**
- Modify: `bot/core/markup_shape.py` (`_coord`, module docstring)
- Test: `tests/test_markup_shape.py`

**Interfaces:**
- Produces: `MARGIN: float = 0.25` in `bot/core/markup_shape.py`; `_coord` accepts
  `-MARGIN <= v <= 1 + MARGIN`.

- [ ] **Step 1: Write the failing tests** in `tests/test_markup_shape.py`:

```python
def test_coordinates_may_sit_in_the_margin_around_the_image():
    kind, shape, _ = parse_mark({"kind": "pen", "points": [[-0.25, 1.25], [1.25, -0.25]]})
    assert json.loads(shape)["points"] == [[-0.25, 1.25], [1.25, -0.25]]

def test_coordinates_beyond_the_margin_are_refused():
    for bad in ([-0.26, 0.5], [0.5, 1.26]):
        with pytest.raises(MarkupRejected):
            parse_mark({"kind": "pen", "points": [[0.5, 0.5], bad]})

def test_a_text_mark_may_sit_in_the_margin():
    _, shape, _ = parse_mark({"kind": "text", "points": [[-0.2, 1.1]], "text": "x", "size": 0.05})
    assert json.loads(shape)["points"] == [[-0.2, 1.1]]
```

The existing `test_coordinates_must_be_in_range_and_not_bools` (uses 1.4) stays unchanged.

- [ ] **Step 2: Run, expect the first and third to FAIL** ("between 0 and 1"):
  `timeout 120 pipenv run python -m pytest tests/test_markup_shape.py -q > /tmp/t1.log 2>&1`

- [ ] **Step 3: Implement.** Add `MARGIN = 0.25` with a comment saying the frontend's
  `MARKUP_MARGIN` must match; widen `_coord`'s check and its message to
  "Coordinates must be between -0.25 and 1.25." Update the module docstring's "normalised 0-1".

- [ ] **Step 4: Run** `tests/test_markup_shape.py tests/test_markup_web.py tests/test_markup_service.py`; expect PASS.

- [ ] **Step 5: Commit** — `feat(markup): accept mark coordinates in a 25% margin around the image`

### Task 2: Editor shows the margin and lets you draw in it

**Files:**
- Modify: `frontend/src/lib/canvasMath.ts` (`normPoint`, new constant)
- Modify: `frontend/src/components/MarkupCanvas.tsx` (layout around the `<img>`, `toNorm`)
- Test: `frontend/src/lib/canvasMath.test.ts`

**Interfaces:**
- Consumes: server range from Task 1.
- Produces: `export const MARKUP_MARGIN = 0.25;` and
  `normPoint(clientX: number, clientY: number, rect: Rect, margin = 0): [number, number] | null`
  — clamps to `[-margin, 1 + margin]`. Default 0 keeps current callers and tests unchanged.

- [ ] **Step 1: Write the failing tests** in the `normPoint` describe block:

```ts
it("with a margin, maps points outside the rect below 0 / above 1", () => {
  // rect 200x100 at (10,20); 25 px left of it is -0.125 of its width
  expect(normPoint(-15, 20, rect, 0.25)).toEqual([-0.125, 0]);
  expect(normPoint(210, 145, rect, 0.25)).toEqual([1, 1.25]);
});
it("with a margin, clamps to the margin's edge", () => {
  expect(normPoint(-999, 999, rect, 0.25)).toEqual([-0.25, 1.25]);
});
```

And in the `polylineHitsCircle` block (W=400, H=100, circle in image pixels):

```ts
it("hits a stroke lying wholly in the margin (negative coordinates)", () => {
  const margin: [number, number][] = [[-0.2, -0.2], [-0.1, -0.2]]; // px (-80,-20)..(-40,-20)
  expect(polylineHitsCircle(margin, W, H, -60, -20, 5)).toBe(true);
  expect(polylineHitsCircle(margin, W, H, -60, 10, 5)).toBe(false);
});
```

Also check in `MarkupCanvas.tsx` how the eraser's `cx, cy` are derived (around line 250):
if they come from anything clamped to 0…1 other than `toNorm`, widen that the same way.

- [ ] **Step 2: Run, expect FAIL:** `cd frontend && timeout 120 npx vitest run src/lib/canvasMath.test.ts > /tmp/t2.log 2>&1`

- [ ] **Step 3: Implement** `MARKUP_MARGIN` and the `margin` parameter in `canvasMath.ts`.
  If the margin hit test already passes, keep it as a guard (it pins that negatives work).

- [ ] **Step 4: Change the layout in `MarkupCanvas.tsx`.** Keep the existing transformed
  div's *inner* box — `<img>`, SVG, text layer — exactly as it is (it stays the image's
  rectangle, which `toNorm` measures). Changes:
  - Wrap that box in a padded div carrying the zoom/pan `transform` instead. Padding in px
    from the measured image size: `${imgH * MARKUP_MARGIN}px ${imgW * MARKUP_MARGIN}px`.
    Background a light neutral shade (Mantine `var(--mantine-color-gray-1)`) with a
    1px dashed border, so it reads as "outside the picture"; the inner image box stays as
    it renders today.
  - Shrink the image cap so image + margin fit the frame: `maxHeight = frameH / (1 + 2*MARKUP_MARGIN)`
    and `maxWidth = frameW / (1 + 2*MARKUP_MARGIN)` px (get `frameW` from the existing
    `useElementSize` on the frame). Until measured (0), fall back to the current values.
  - SVG: add `overflow: "visible"` so strokes outside the viewBox draw.
  - `toNorm`: pass `MARKUP_MARGIN` to `normPoint`.
  - Update the "Three nested boxes" comment to describe the new padded wrapper.

- [ ] **Step 5: Verify:** `cd frontend && timeout 180 npm run build > /tmp/b.log 2>&1` and
  `timeout 180 npx vitest run > /tmp/v.log 2>&1` both pass (compare the skipped count with before).

- [ ] **Step 6: Manual check (hand to the user — they test on their desktop, not this machine).**
  Open an image that already has marks: they sit where they did. Draw pen, line, arrow,
  highlighter and text partly and wholly in each margin; reload — they persist. Erase one in
  the margin. Zoom in/out and pan: margin and marks move with the image.

- [ ] **Step 7: Commit** — `feat(markup): draw in a blank margin around the image`

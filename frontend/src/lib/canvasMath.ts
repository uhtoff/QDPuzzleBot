/** Pure coordinate maths for the canvas viewer and the markup overlay.
 *
 * Extracted from CanvasView/MarkupCanvas so it can be tested without a DOM.
 * Nothing here reads the document: callers pass in the rect they measured and
 * keep every getBoundingClientRect/ref where it was.
 */

/** Zoom limits. Below 0.02 a big sheet is a smudge; above 4 the tiles are
 *  being upscaled past their own resolution. */
export const MIN_SCALE = 0.02;
export const MAX_SCALE = 4;

export type Point = { x: number; y: number };
export type ViewTransform = { scale: number; offset: Point };
export type Rect = { left: number; top: number; width: number; height: number };

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

/** Zoom about an arbitrary anchor (in wrapper-local pixels), keeping whatever
 *  is under the anchor fixed on screen. The button controls anchor on the
 *  viewport centre; wheel and pinch anchor on the cursor/fingers. */
export function zoomAt(
  { scale, offset }: ViewTransform,
  factor: number,
  anchorX: number,
  anchorY: number,
): ViewTransform {
  const next = clampScale(scale * factor);
  return {
    scale: next,
    offset: {
      x: anchorX - ((anchorX - offset.x) * next) / scale,
      y: anchorY - ((anchorY - offset.y) * next) / scale,
    },
  };
}

/** The on-screen box for tile (x, y) at a displayed tile size of `tileDisp`.
 *
 *  Each edge is snapped to a whole pixel, and a tile's far edge is computed
 *  from the SAME expression as its neighbour's near edge -- so adjacent tiles
 *  abut exactly at fractional scales, with no seam and no overlap. Sizing a
 *  tile by rounding tileDisp itself instead would accumulate error across the
 *  grid and reopen the hairline gaps.
 */
export function tileRect(x: number, y: number, tileDisp: number): Rect {
  const left = Math.round(x * tileDisp);
  const top = Math.round(y * tileDisp);
  return {
    left,
    top,
    width: Math.round((x + 1) * tileDisp) - left,
    height: Math.round((y + 1) * tileDisp) - top,
  };
}

/** A client point as normalised [0..1] image coordinates within `rect`.
 *  Null for a zero-size rect, where the ratio would be NaN or Infinity. */
export function normPoint(
  clientX: number,
  clientY: number,
  rect: Rect,
): [number, number] | null {
  if (!rect.width || !rect.height) return null;
  return [
    Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)),
    Math.min(1, Math.max(0, (clientY - rect.top) / rect.height)),
  ];
}

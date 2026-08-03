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

/** Distance from point p to the segment ab, everything in the same units. */
function distToSegment(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  // A degenerate segment (both ends coincide) is just a point.
  const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / len2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Does a normalised polyline pass within `radius` of the circle at (cx, cy)?
 *
 *  The polyline is stored in [0..1] image coordinates but the eraser is a
 *  circle on screen, so the comparison has to happen in pixels: a normalised
 *  radius would be an ellipse on any non-square image, erasing more
 *  horizontally than vertically. Caller passes the image's rendered size and
 *  the circle in those same pixels.
 *
 *  Segments count, not just vertices — a long straight stroke has two points
 *  metres apart, and dragging the eraser across its middle must still hit it.
 */
export function polylineHitsCircle(
  points: [number, number][],
  imgW: number, imgH: number,
  cx: number, cy: number, radius: number,
): boolean {
  if (points.length === 0) return false;
  const at = (i: number): [number, number] => [points[i][0] * imgW, points[i][1] * imgH];
  if (points.length === 1) {
    const [x, y] = at(0);
    return Math.hypot(cx - x, cy - y) <= radius;
  }
  for (let i = 1; i < points.length; i++) {
    const [ax, ay] = at(i - 1);
    const [bx, by] = at(i);
    if (distToSegment(cx, cy, ax, ay, bx, by) <= radius) return true;
  }
  return false;
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

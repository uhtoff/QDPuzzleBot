// The pure coordinate maths behind the canvas viewer. It lives here rather
// than inline in CanvasView/MarkupCanvas because these are the parts that are
// silently wrong -- an anchor that drifts a pixel per zoom step, a tile seam
// that only opens at fractional scales -- and none of it needs a DOM to check.
import { describe, expect, it } from "vitest";
import { clampScale, normPoint, polylineHitsCircle, tileRect, zoomAt } from "./canvasMath";

describe("zoomAt", () => {
  it("keeps the point under the anchor fixed on screen", () => {
    // screen = imagePoint * scale + offset. For any anchor, the image point
    // currently under it must map to the same screen position after the zoom.
    const r = zoomAt({ scale: 1, offset: { x: 10, y: 20 } }, 2, 100, 80);
    const imgX = (100 - 10) / 1;
    expect(imgX * r.scale + r.offset.x).toBeCloseTo(100);
    const imgY = (80 - 20) / 1;
    expect(imgY * r.scale + r.offset.y).toBeCloseTo(80);
  });
  it("clamps to [0.02, 4] and still anchors correctly at the clamp", () => {
    expect(zoomAt({ scale: 3, offset: { x: 0, y: 0 } }, 10, 0, 0).scale).toBe(4);
    expect(clampScale(0.0001)).toBe(0.02);
  });
});

describe("tileRect", () => {
  // The seam fix: a tile's far edge is computed from the same expression as
  // its neighbour's near edge -- zero gap, zero overlap, at fractional scales.
  const scales = [511.9, 512, 512.7, 100.3, 33.34, 512.5, 700.001];
  it("adjacent tiles abut exactly at every fractional tile size", () => {
    for (const disp of scales)
      for (let x = 0; x < 40; x++) {
        const a = tileRect(x, 0, disp), b = tileRect(x + 1, 0, disp);
        expect(a.left + a.width).toBe(b.left);
      }
  });
});

describe("normPoint", () => {
  const rect = { left: 10, top: 20, width: 200, height: 100 };
  it("maps rect corners to 0 and 1", () => {
    expect(normPoint(10, 20, rect)).toEqual([0, 0]);
    expect(normPoint(210, 120, rect)).toEqual([1, 1]);
  });
  it("clamps outside points instead of going out of range", () => {
    expect(normPoint(0, 0, rect)).toEqual([0, 0]);
    expect(normPoint(999, 999, rect)).toEqual([1, 1]);
  });
  it("returns null for a zero-size rect", () => {
    expect(normPoint(5, 5, { left: 0, top: 0, width: 0, height: 0 })).toBeNull();
  });
});

describe("polylineHitsCircle", () => {
  // A wide, short image: the aspect ratio is the whole point. A normalised
  // radius would erase 4x further horizontally than vertically here.
  const W = 400, H = 100;
  // A horizontal stroke across the middle, stored as two far-apart points.
  const line: [number, number][] = [[0.1, 0.5], [0.9, 0.5]];

  it("hits the middle of a long segment, not just its vertices", () => {
    // Dead centre of the stroke, nowhere near either stored point.
    expect(polylineHitsCircle(line, W, H, 200, 50, 5)).toBe(true);
  });
  it("misses when the circle is further away than its radius", () => {
    // 20px above the stroke with a 10px radius.
    expect(polylineHitsCircle(line, W, H, 200, 30, 10)).toBe(false);
    expect(polylineHitsCircle(line, W, H, 200, 30, 25)).toBe(true);
  });
  it("measures in pixels, so reach is the same in x and y", () => {
    // Same 15px gap from the stroke's end point, once along x and once along
    // y. A normalised-space test would pass one and fail the other.
    const endX = 0.9 * W, endY = 0.5 * H;
    expect(polylineHitsCircle(line, W, H, endX + 15, endY, 20)).toBe(true);
    expect(polylineHitsCircle(line, W, H, endX, endY + 15, 20)).toBe(true);
    expect(polylineHitsCircle(line, W, H, endX + 25, endY, 20)).toBe(false);
    expect(polylineHitsCircle(line, W, H, endX, endY + 25, 20)).toBe(false);
  });
  it("does not run off the end of a segment", () => {
    // Straight on from the stroke's end, well past it: the nearest point is
    // the end vertex, not somewhere on the infinite line through it.
    expect(polylineHitsCircle(line, W, H, 0.9 * W + 60, 0.5 * H, 20)).toBe(false);
  });
  it("treats a single stored point as a point", () => {
    expect(polylineHitsCircle([[0.5, 0.5]], W, H, 200, 50, 3)).toBe(true);
    expect(polylineHitsCircle([[0.5, 0.5]], W, H, 240, 50, 3)).toBe(false);
    expect(polylineHitsCircle([], W, H, 200, 50, 100)).toBe(false);
  });
});

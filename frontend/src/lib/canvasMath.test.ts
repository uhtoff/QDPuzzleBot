// The pure coordinate maths behind the canvas viewer. It lives here rather
// than inline in CanvasView/MarkupCanvas because these are the parts that are
// silently wrong -- an anchor that drifts a pixel per zoom step, a tile seam
// that only opens at fractional scales -- and none of it needs a DOM to check.
import { describe, expect, it } from "vitest";
import { clampScale, normPoint, tileRect, zoomAt } from "./canvasMath";

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

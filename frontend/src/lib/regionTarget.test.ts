// A freshly drawn shape is saved as a region before its puzzle is created. When
// that create fails or is cancelled the modal stays open on the same shape, and
// every later save path must reuse that region rather than save the shape again.
import { describe, expect, it } from "vitest";
import { existingRegion } from "./regionTarget";
import type { CanvasRegion, RegionShape } from "../types";

const shape = (x: number): RegionShape => ({ type: "rect", points: [[x, 0], [x + 0.1, 0.1]] });
const regionFor = (id: number, s: RegionShape) =>
  ({ id, shape: s, label: "", note: null, puzzle_id: null, role: null }) as unknown as CanvasRegion;

describe("existingRegion", () => {
  it("an opened region is itself", () => {
    const r = regionFor(1, shape(0));
    expect(existingRegion(r, null, null)).toBe(r);
  });

  it("a drawn shape already saved is its saved region", () => {
    const s = shape(0);
    const saved = regionFor(7, s);
    expect(existingRegion(null, s, { shape: s, region: saved })).toBe(saved);
  });

  it("a different drawn shape is not the saved one's region", () => {
    const saved = regionFor(7, shape(0));
    expect(existingRegion(null, shape(0.5), { shape: saved.shape, region: saved })).toBeNull();
  });

  it("a drawn shape never saved has no region yet", () => {
    expect(existingRegion(null, shape(0), null)).toBeNull();
  });
});

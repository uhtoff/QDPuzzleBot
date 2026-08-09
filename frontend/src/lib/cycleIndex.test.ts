import { describe, it, expect } from "vitest";
import { cycleIndex } from "./cycleIndex";

describe("cycleIndex", () => {
  it("steps forward", () => {
    expect(cycleIndex(0, 1, 5)).toBe(1);
    expect(cycleIndex(3, 1, 5)).toBe(4);
  });

  it("wraps forward off the end", () => {
    expect(cycleIndex(4, 1, 5)).toBe(0);
  });

  it("wraps BACKWARD off the start", () => {
    // The case a plain `%` gets wrong: (0 - 1) % 5 === -1 in JS, which would
    // index off the end of the array and hand back undefined.
    expect(cycleIndex(0, -1, 5)).toBe(4);
  });

  it("steps backward", () => {
    expect(cycleIndex(3, -1, 5)).toBe(2);
  });

  it("recovers from an indexOf miss", () => {
    expect(cycleIndex(-1, 1, 5)).toBe(0);
  });

  it("does not divide by zero on an empty list", () => {
    expect(cycleIndex(0, 1, 0)).toBe(0);
  });
});

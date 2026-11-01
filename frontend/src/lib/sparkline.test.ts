// Bar height is chat RATE (messages per minute of the bar) on one scale shared
// by every puzzle, so a trickle stays short and a burst is tall whatever the
// puzzle's age. Per-puzzle normalisation would draw a trickle at full height.
import { describe, expect, it } from "vitest";
import { barHeight, latestActivity } from "./sparkline";

describe("barHeight", () => {
  it("is zero with no chat", () => {
    expect(barHeight(0, 5)).toBe(0);
  });
  it("tops out at one message a minute", () => {
    expect(barHeight(5, 5)).toBe(1);
    expect(barHeight(1000, 5)).toBe(1);
  });
  it("is the square root of the rate below that", () => {
    expect(barHeight(1, 60)).toBeCloseTo(Math.sqrt(1 / 60));
  });
  it("draws the same count shorter over a longer bar", () => {
    expect(barHeight(5, 60)).toBeLessThan(barHeight(5, 10));
  });
  it("is full height for a zero-length bar rather than NaN", () => {
    expect(barHeight(3, 0)).toBe(1);
  });
});

describe("latestActivity", () => {
  const a = "2026-11-01T12:00:00+00:00";
  const b = "2026-11-01T12:30:00+00:00";
  const c = "2026-11-01T12:10:00+00:00";
  it("picks the latest of the three whichever slot it is in", () => {
    expect(latestActivity(a, b, c)).toBe(b);
    expect(latestActivity(b, a, c)).toBe(b);
    expect(latestActivity(a, c, b)).toBe(b);
  });
  it("ignores missing values", () => {
    expect(latestActivity(null, undefined, a)).toBe(a);
  });
  it("is null when there is nothing", () => {
    expect(latestActivity(null, null, undefined)).toBeNull();
  });
});

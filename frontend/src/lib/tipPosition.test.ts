import { describe, expect, it } from "vitest";
import { tipPosition } from "./tipPosition";

const tip = { width: 100, height: 20 };

describe("tipPosition", () => {
  it("centres above the anchor when there is room", () => {
    const pos = tipPosition({ top: 200, bottom: 220, left: 300, width: 40 }, tip, 1000);
    expect(pos).toEqual({ left: 270, top: 174 });
  });

  it("flips below when the anchor is near the top of the viewport", () => {
    const pos = tipPosition({ top: 10, bottom: 30, left: 300, width: 40 }, tip, 1000);
    expect(pos).toEqual({ left: 270, top: 36 });
  });

  it("stays inside the left and right edges", () => {
    expect(tipPosition({ top: 200, bottom: 220, left: 0, width: 20 }, tip, 1000).left).toBe(4);
    expect(tipPosition({ top: 200, bottom: 220, left: 980, width: 20 }, tip, 1000).left).toBe(896);
  });
});

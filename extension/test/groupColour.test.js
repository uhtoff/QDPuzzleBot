import { describe, it, expect } from "vitest";
import { GROUP_COLOURS, groupColour } from "../src/lib/groupColour.js";

describe("groupColour", () => {
  it("returns a member of GROUP_COLOURS, never grey", () => {
    for (const name of ["Round One", "Round 7", "Meta", "x", "Ünïcode"]) {
      const c = groupColour(name);
      expect(GROUP_COLOURS).toContain(c);
      expect(c).not.toBe("grey");
    }
  });

  it("is stable for the same name and ignores surrounding space and case", () => {
    expect(groupColour("Round One")).toBe(groupColour("Round One"));
    expect(groupColour(" Round One ")).toBe(groupColour("round one"));
  });

  it("spreads twelve sequential round names over at least 4 distinct colours", () => {
    const names = Array.from({ length: 12 }, (_, i) => `Round ${i + 1}`);
    const distinct = new Set(names.map(groupColour));
    expect(distinct.size).toBeGreaterThanOrEqual(4);
  });

  it("returns a member of GROUP_COLOURS for empty, null and undefined without throwing", () => {
    for (const name of ["", null, undefined]) {
      expect(GROUP_COLOURS).toContain(groupColour(name));
    }
  });
});

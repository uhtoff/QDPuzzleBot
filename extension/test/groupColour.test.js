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

import { pickColour, findRoundGroup } from "../src/lib/groupColour.js";

describe("pickColour", () => {
  const h = groupColour("Office");
  const i = GROUP_COLOURS.indexOf(h);
  const at = (n) => GROUP_COLOURS[(i + n) % GROUP_COLOURS.length];

  it("remembered wins", () => {
    const other = GROUP_COLOURS.find((c) => c !== h);
    expect(other).not.toBe(h);
    expect(pickColour("Office", [h], other)).toBe(other);
  });
  it("hash colour when free", () => {
    expect(pickColour("Office", [at(1)], undefined)).toBe(h);
  });
  it("next free from the hash position, wrapping", () => {
    expect(pickColour("Office", [h, at(1)], undefined)).toBe(at(2));
    // "H" hashes to the last colour (index 7), so the walk must wrap to index 0.
    expect(GROUP_COLOURS.indexOf(groupColour("H"))).toBe(7);
    const last = GROUP_COLOURS[7];
    expect(pickColour("H", [last], undefined)).toBe(GROUP_COLOURS[0]);
    expect(pickColour("H", [last, GROUP_COLOURS[0]], undefined)).toBe(GROUP_COLOURS[1]);
  });
  it("all taken: least used, ties earliest from hash", () => {
    const used = [...GROUP_COLOURS, ...GROUP_COLOURS.filter((c) => c !== at(3))];
    expect(pickColour("Office", used, undefined)).toBe(at(3));
    expect(pickColour("Office", [...GROUP_COLOURS], undefined)).toBe(h);
  });
  it("is deterministic", () => {
    expect(pickColour("Office", [h], undefined)).toBe(pickColour("Office", [h], undefined));
  });
});

describe("findRoundGroup", () => {
  it("matches title trimmed and case-insensitively", () => {
    const gs = [{ id: 1, title: "Other" }, { id: 2, title: " office " }];
    expect(findRoundGroup("Office", gs).id).toBe(2);
    expect(findRoundGroup("Nope", gs)).toBeNull();
    expect(findRoundGroup("", [{ id: 3, title: "" }])).toBeNull();
    expect(findRoundGroup("  ", [{ id: 3, title: " " }])).toBeNull();
  });
});

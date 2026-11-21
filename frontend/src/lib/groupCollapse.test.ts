import { describe, expect, it } from "vitest";
import { NO_OVERRIDES, applyOverrides, deriveOverrides, parseStoredCollapse } from "./groupCollapse";

// Groups 1 and 2 are unsolved (open by default); 3 and 4 are solved (closed).
const DEFAULT_OPEN = ["1", "2"];
const VISIBLE = ["1", "2", "3", "4"];

describe("applyOverrides", () => {
  it("is the default when nothing was overridden", () => {
    expect(applyOverrides(DEFAULT_OPEN, VISIBLE, NO_OVERRIDES)).toEqual(["1", "2"]);
  });

  it("closes a default-open group and opens a default-closed one", () => {
    expect(applyOverrides(DEFAULT_OPEN, VISIBLE, { opened: ["4"], closed: ["1"] })).toEqual(["2", "4"]);
  });

  it("opens a group that did not exist when the overrides were saved", () => {
    expect(applyOverrides(["1", "2", "9"], [...VISIBLE, "9"], { opened: [], closed: ["1"] })).toEqual(["2", "9"]);
  });

  it("ignores overrides for groups that are not visible", () => {
    expect(applyOverrides(DEFAULT_OPEN, ["1", "2"], { opened: ["4", "77"], closed: [] })).toEqual(["1", "2"]);
  });
});

describe("deriveOverrides", () => {
  it("records only differences from the default", () => {
    // Person collapses 1 and opens 3; 2 and 4 are left at their defaults.
    expect(deriveOverrides(NO_OVERRIDES, DEFAULT_OPEN, VISIBLE, ["2", "3"])).toEqual({
      opened: ["3"],
      closed: ["1"],
    });
  });

  it("drops an override when the group goes back to its default", () => {
    const prev = { opened: ["3"], closed: ["1"] };
    expect(deriveOverrides(prev, DEFAULT_OPEN, VISIBLE, ["1", "2"])).toEqual(NO_OVERRIDES);
  });

  it("collapse all closes the default-open groups and clears the opened ones", () => {
    expect(deriveOverrides({ opened: ["3"], closed: [] }, DEFAULT_OPEN, VISIBLE, [])).toEqual({
      opened: [],
      closed: ["1", "2"],
    });
  });

  it("keeps overrides for groups that are not on screen", () => {
    // Only groups 2 and 3 are visible (say a status filter hides the rest).
    const prev = { opened: ["4"], closed: ["1"] };
    expect(deriveOverrides(prev, DEFAULT_OPEN, ["2", "3"], ["3"])).toEqual({
      opened: ["4", "3"],
      closed: ["1", "2"],
    });
  });

  it("round-trips through applyOverrides", () => {
    const next = ["2", "4"];
    const o = deriveOverrides(NO_OVERRIDES, DEFAULT_OPEN, VISIBLE, next);
    expect(applyOverrides(DEFAULT_OPEN, VISIBLE, o)).toEqual(next);
  });
});

describe("parseStoredCollapse", () => {
  it("reads back what was stored", () => {
    expect(parseStoredCollapse('{"opened":["3"],"closed":["1","2"]}')).toEqual({ opened: ["3"], closed: ["1", "2"] });
  });

  it("falls back to no overrides for anything unreadable", () => {
    for (const raw of [null, "", "not json", "[]", "7", '{"opened":"3"}']) {
      expect(parseStoredCollapse(raw)).toEqual(NO_OVERRIDES);
    }
  });

  it("drops non-string entries", () => {
    expect(parseStoredCollapse('{"opened":[3,"4"],"closed":[null]}')).toEqual({ opened: ["4"], closed: [] });
  });
});

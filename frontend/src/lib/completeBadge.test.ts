import { describe, expect, it } from "vitest";
import { completeLabel, ordinal } from "./completeBadge";

describe("ordinal", () => {
  it("handles the teens and the 1/2/3 endings", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 111].map(ordinal)).toEqual(
      ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "111th"]);
  });
});

describe("completeLabel (the hunt board's badge)", () => {
  const mt = { name: "M", completed: true, archived: false, team: "T", place: 3, team_count: 5 };
  it("a finished team: Complete and its place", () => {
    expect(completeLabel(mt)).toBe("Complete · 3rd of 5");
  });
  it("no place known: just Complete", () => {
    expect(completeLabel({ ...mt, place: null })).toBe("Complete");
  });
  it("not finished, or not a multi-team hunt: no badge", () => {
    expect(completeLabel({ ...mt, completed: false })).toBeNull();
    expect(completeLabel(null)).toBeNull();
  });
});

// Priority ordering is a RELATIONAL property, so every case is asserted from
// one sort of the whole vocabulary rather than value by value: a single puzzle
// cannot have a wrong order. The vocabulary is bot/core/constants.py's
// PRIORITIES (["low", "medium", "high", "very high"], constants.py:37), which
// the dashboard picker mirrors in PuzzleEditCells.PRIORITY_OPTIONS.
//
// The rank table used to list only {high, low} and send everything else through
// a `?? medium` default, so "very high" — the one value whose whole point is to
// outrank "high" — sorted below it, indistinguishable from "medium".
import { describe, expect, it } from "vitest";
import { priorityColor, priorityRank } from "./priority";

// Deliberately scrambled: an input already in rank order would pass against an
// implementation that ranked nothing at all.
const SCRAMBLED: (string | null)[] = [
  "low",
  null,
  "very high",
  "medium",
  "aggressively urgent", // not in PRIORITIES — the explicit unknown fallback
  "high",
];

describe("priorityRank", () => {
  it("orders the whole vocabulary most urgent first, unset last", () => {
    const sorted = [...SCRAMBLED].sort((a, b) => priorityRank(a) - priorityRank(b));
    // "aggressively urgent" ties with "medium" (the documented fallback) and
    // Array.prototype.sort is stable, so it keeps its input position behind it.
    expect(sorted).toEqual([
      "very high",
      "high",
      "medium",
      "aggressively urgent",
      "low",
      null,
    ]);
  });

  it("ranks an unrecognised priority as medium, not as most or least urgent", () => {
    expect(priorityRank("aggressively urgent")).toBe(priorityRank("medium"));
    expect(priorityRank("aggressively urgent")).toBeGreaterThan(priorityRank("high"));
    expect(priorityRank("aggressively urgent")).toBeLessThan(priorityRank("low"));
  });

  it("ranks legacy Title Case rows the same as canonical lowercase", () => {
    // Rows written before migration 041 may still hold "Very High"/"High".
    expect(priorityRank("Very High")).toBe(priorityRank("very high"));
    expect(priorityRank("High")).toBe(priorityRank("high"));
  });

  it("sorts an unset priority behind every set one, including low", () => {
    expect(priorityRank(null)).toBeGreaterThan(priorityRank("low"));
    expect(priorityRank("")).toBe(priorityRank(null));
  });
});

describe("priorityColor", () => {
  it("gives each end of the vocabulary a distinct colour", () => {
    expect(priorityColor("low")).toBe("gray");
    expect(priorityColor("medium")).toBe("blue");
    expect(priorityColor("high")).toBe("red");
  });

  it("does not render 'very high' with the calm middle-of-the-road colour", () => {
    // The badge that shouted loudest used to be the one below it: "very high"
    // fell through to the default and came out the same blue as "medium".
    expect(priorityColor("very high")).not.toBe(priorityColor("medium"));
    expect(priorityColor("very high")).toBe("red");
    expect(priorityColor("Very High")).toBe(priorityColor("very high"));
  });

  it("colours an unrecognised or unset priority as medium", () => {
    expect(priorityColor("aggressively urgent")).toBe(priorityColor("medium"));
    expect(priorityColor(null)).toBe(priorityColor("medium"));
  });
});

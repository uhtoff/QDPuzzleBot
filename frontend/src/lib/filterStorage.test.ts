import { describe, expect, it } from "vitest";
import { EMPTY_FILTER } from "./puzzleFilter";
import { parseStoredFilter, serialiseFilter } from "./filterStorage";

describe("stored board filter", () => {
  it("round-trips the settings but never the search", () => {
    const f = { search: "crossword", statuses: ["stuck", "new"], priorities: ["high"], types: ["word", "meta"], hideSolved: true };
    expect(parseStoredFilter(serialiseFilter(f))).toEqual({ ...f, search: "" });
  });

  it("keeps hide solved off when it was off", () => {
    const f = { ...EMPTY_FILTER, statuses: ["stuck"] };
    expect(parseStoredFilter(serialiseFilter(f)).hideSolved).toBe(false);
  });

  it("treats missing or damaged storage as no filter", () => {
    expect(parseStoredFilter(null)).toEqual(EMPTY_FILTER);
    expect(parseStoredFilter("{not json")).toEqual(EMPTY_FILTER);
    expect(parseStoredFilter("42")).toEqual(EMPTY_FILTER);
    expect(parseStoredFilter('{"statuses":"stuck","hideSolved":"yes","types":["word",3]}')).toEqual({
      ...EMPTY_FILTER,
      types: ["word"],
    });
  });
});

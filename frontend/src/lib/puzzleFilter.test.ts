import { describe, expect, it } from "vitest";
import { EMPTY_FILTER, filterActive, puzzleMatches, type PuzzleFilter } from "./puzzleFilter";

type P = Parameters<typeof puzzleMatches>[0];
const puzzle = (over: Partial<P>): P => ({
  name: "Plain",
  solution: null,
  solved: false,
  status: null,
  priority: null,
  puzzle_type: null,
  rounds: [],
  ...over,
});
const f = (over: Partial<PuzzleFilter>): PuzzleFilter => ({ ...EMPTY_FILTER, ...over });

describe("puzzleMatches", () => {
  it("lets everything through the empty filter", () => {
    expect(puzzleMatches(puzzle({ solved: true }), EMPTY_FILTER)).toBe(true);
    expect(filterActive(EMPTY_FILTER)).toBe(false);
  });

  it("searches name, answer and round names, each on its own", () => {
    const p = puzzle({ name: "World’s Best Crossword", solution: "FUR COAT", rounds: [{ name: "Ocean" }] });
    expect(puzzleMatches(p, f({ search: "world's best" }))).toBe(true);
    expect(puzzleMatches(p, f({ search: "fur coat" }))).toBe(true);
    expect(puzzleMatches(p, f({ search: "ocean" }))).toBe(true);
    expect(puzzleMatches(p, f({ search: "coatocean" }))).toBe(false); // answer + round run together
    expect(puzzleMatches(p, f({ search: "desert" }))).toBe(false);
  });

  it("hides solved puzzles only when asked", () => {
    expect(puzzleMatches(puzzle({ solved: true }), f({ hideSolved: true }))).toBe(false);
    expect(puzzleMatches(puzzle({ solved: false }), f({ hideSolved: true }))).toBe(true);
  });

  it("matches any chosen status, priority or type, and all three together", () => {
    const stuck = puzzle({ status: "stuck", priority: "high", puzzle_type: "word" });
    expect(puzzleMatches(stuck, f({ statuses: ["new", "stuck"] }))).toBe(true);
    expect(puzzleMatches(stuck, f({ statuses: ["new"] }))).toBe(false);
    expect(puzzleMatches(stuck, f({ priorities: ["low"] }))).toBe(false);
    expect(puzzleMatches(stuck, f({ types: ["word"], priorities: ["high"] }))).toBe(true);
    expect(puzzleMatches(stuck, f({ types: ["word"], priorities: ["low"] }))).toBe(false);
    expect(puzzleMatches(puzzle({ status: null }), f({ statuses: ["stuck"] }))).toBe(false);
  });

  it("counts any filter as active", () => {
    expect(filterActive(f({ search: "  x " }))).toBe(true);
    expect(filterActive(f({ search: "   " }))).toBe(false);
    expect(filterActive(f({ hideSolved: true }))).toBe(true);
    expect(filterActive(f({ types: ["word"] }))).toBe(true);
  });
});

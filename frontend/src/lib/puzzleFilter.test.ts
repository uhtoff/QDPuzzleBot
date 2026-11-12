import { describe, expect, it } from "vitest";
import { boardGroupRows, EMPTY_FILTER, filterActive, puzzleMatches, type PuzzleFilter } from "./puzzleFilter";

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

describe("boardGroupRows (By group)", () => {
  // Name That Tune's feeders sit in a group named after it; one of them is also in Aviaria.
  const tune = puzzle({ name: "Name That Tune", rounds: [{ name: "Name That Tune" }] });
  const birds = puzzle({ name: "Bird Calls", solution: "FIREBIRD", rounds: [{ name: "Name That Tune" }, { name: "Aviaria" }] });
  const jump = puzzle({ name: "Jumping to Conclusions", solved: true, rounds: [{ name: "Name That Tune" }] });
  const names = (ps: P[]) => ps.map((p) => p.name);

  it("shows every row when nothing is filtered", () => {
    expect(names(boardGroupRows("Aviaria", [birds], EMPTY_FILTER))).toEqual(["Bird Calls"]);
  });

  it("matches puzzles on name and answer, not on the round names they carry", () => {
    expect(names(boardGroupRows("Aviaria", [birds], f({ search: "name" })))).toEqual([]);
    expect(names(boardGroupRows("Aviaria", [birds], f({ search: "firebird" })))).toEqual(["Bird Calls"]);
  });

  it("shows a whole group whose own name matches", () => {
    expect(names(boardGroupRows("Name That Tune", [tune, birds, jump], f({ search: "name" })))).toEqual([
      "Name That Tune", "Bird Calls", "Jumping to Conclusions",
    ]);
  });

  it("still applies the other filters to a group whose name matches", () => {
    expect(names(boardGroupRows("Name That Tune", [tune, birds, jump], f({ search: "name", hideSolved: true })))).toEqual([
      "Name That Tune", "Bird Calls",
    ]);
  });

  it("finds a puzzle in a group whose name does not match", () => {
    expect(names(boardGroupRows("Name That Tune", [tune, birds, jump], f({ search: "jumping" })))).toEqual([
      "Jumping to Conclusions",
    ]);
  });
});

// One board→puzzles flatten. Five components each hand-rolled the
// "walk every group, dedupe puzzles by id" loop (one via an O(n²)
// findIndex); these pin the shared helper's dedupe and ordering.
import { describe, expect, it } from "vitest";
import { puzzleOptions, puzzlesById } from "./boardPuzzles";
import type { BoardGroup, Puzzle } from "../types";

const puzzle = (id: number, name: string) => ({ id, name }) as Puzzle;
const group = (id: number, puzzles: Puzzle[]) =>
  ({ tag_group_id: id, puzzles }) as BoardGroup;

const BOARD = [
  group(1, [puzzle(10, "Alpha"), puzzle(11, "Bravo")]),
  // 11 is tagged into both groups; it must appear once.
  group(2, [puzzle(11, "Bravo"), puzzle(12, "Charlie")]),
];

describe("puzzlesById", () => {
  it("dedupes a puzzle tagged into several groups", () => {
    const byId = puzzlesById(BOARD);
    expect(Array.from(byId.keys())).toEqual([10, 11, 12]);
    expect(byId.get(11)?.name).toBe("Bravo");
  });
});

describe("puzzleOptions", () => {
  it("keeps board order and string values", () => {
    expect(puzzleOptions(BOARD)).toEqual([
      { value: "10", label: "Alpha" },
      { value: "11", label: "Bravo" },
      { value: "12", label: "Charlie" },
    ]);
  });
});

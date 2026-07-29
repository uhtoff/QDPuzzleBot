import type { BoardGroup, Puzzle } from "../types";

/** Every puzzle on the board, deduplicated by id. A puzzle tagged into
 *  several groups appears once. */
export function puzzlesById(board: BoardGroup[]): Map<number, Puzzle> {
  const byId = new Map<number, Puzzle>();
  board.forEach((g) => g.puzzles.forEach((p) => byId.set(p.id, p)));
  return byId;
}

/** Select options for every puzzle on the board, deduplicated, in board
 *  order. Callers wanting alphabetical order sort the result. */
export function puzzleOptions(board: BoardGroup[]): { value: string; label: string }[] {
  return Array.from(puzzlesById(board).values()).map((p) => ({
    value: String(p.id),
    label: p.name,
  }));
}

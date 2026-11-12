// The puzzle search/filter shared by the "By group" and "All puzzles" views.
import type { Puzzle } from "../types";
import { matchesSearch } from "./searchText";

export interface PuzzleFilter {
  search: string;
  statuses: string[];
  priorities: string[];
  types: string[];
  hideSolved: boolean;
}

export const EMPTY_FILTER: PuzzleFilter = { search: "", statuses: [], priorities: [], types: [], hideSolved: false };

type Filterable = Pick<Puzzle, "name" | "solution" | "solved" | "status" | "priority" | "puzzle_type"> & {
  rounds: { name: string }[];
};

export function filterActive(f: PuzzleFilter): boolean {
  return (
    f.search.trim() !== "" || f.hideSolved || f.statuses.length > 0 || f.priorities.length > 0 || f.types.length > 0
  );
}

export function puzzleMatches(p: Filterable, f: PuzzleFilter): boolean {
  if (f.hideSolved && p.solved) return false;
  if (f.statuses.length && !f.statuses.includes(p.status || "")) return false;
  if (f.priorities.length && !f.priorities.includes(p.priority || "")) return false;
  if (f.types.length && !f.types.includes(p.puzzle_type || "")) return false;
  const q = f.search.trim();
  if (q) {
    // Each field separately: folding strips spaces, so one joined string would let a
    // query match across the name/answer/round boundary.
    const fields = [p.name, p.solution ?? "", ...p.rounds.map((r) => r.name)];
    if (!fields.some((field) => matchesSearch(field, q))) return false;
  }
  return true;
}

/** The distinct non-empty values of one field, sorted: the filter's options. */
export function uniqueValues(puzzles: Puzzle[], key: "status" | "priority" | "puzzle_type"): string[] {
  const set = new Set<string>();
  puzzles.forEach((p) => {
    if (p[key]) set.add(p[key] as string);
  });
  return Array.from(set).sort();
}

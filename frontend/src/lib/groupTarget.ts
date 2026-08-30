import type { BoardGroup, Puzzle } from "../types";

/**
 * A TagGroup's OWN target puzzle, or null if it has none.
 *
 * `target_puzzle_id` is the only source of truth. Do NOT reach for the first
 * member with `is_meta`: the board payload sets `is_meta` from the puzzle's
 * `is_target` column (bot/web/data.py), which means "is the target of ANY
 * group", not "is this group's target". In a metameta — whose members are by
 * definition other groups' targets — every member has `is_meta` true, and
 * `group.puzzles` arrives in the SQL order (is_target DESC, solved ASC,
 * id ASC), so the later-created own target usually sorts behind the members'
 * targets and the first `is_meta` hit is a FOREIGN group's target.
 *
 * A group with no target is legitimate (a view / holding pen such as
 * Unassigned Feeders), and the target may also be absent from `puzzles` on a
 * filtered board; both give null rather than a wrong puzzle.
 */
export function groupTarget(group: BoardGroup): Puzzle | null {
  if (group.target_puzzle_id == null) return null;
  return group.puzzles.find((p) => p.id === group.target_puzzle_id) ?? null;
}

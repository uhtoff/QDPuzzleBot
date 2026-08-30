// A TagGroup's OWN target puzzle. The board payload's `is_meta` is
// `bool(row["is_target"])` (bot/web/data.py) — "is the target of ANY group",
// not "is this group's target" — so picking the first `is_meta` member is
// wrong for a metameta, whose members are by definition other groups' targets.
// These pin the selection against that misreading.
import { describe, expect, it } from "vitest";
import { groupTarget } from "./groupTarget";
import type { BoardGroup, Puzzle } from "../types";

const puzzle = (id: number, name: string, is_meta = false) =>
  ({ id, name, is_meta }) as Puzzle;
const group = (target_puzzle_id: number | null, puzzles: Puzzle[]) =>
  ({ tag_group_id: 1, name: "G", target_puzzle_id, puzzles }) as BoardGroup;

describe("groupTarget", () => {
  it("picks the group's OWN target in a metameta, not the first member target", () => {
    // A metameta: every member is some other group's target, so every member
    // has is_meta true. `group.puzzles` arrives in the SQL order
    // (is_target DESC, solved ASC, id ASC), and the metameta's own target was
    // created last, so it sorts LAST among the targets — the first is_meta
    // member is a foreign group's target.
    const feederMetaA = puzzle(10, "Round One Meta", true);
    const feederMetaB = puzzle(20, "Round Two Meta", true);
    const ownTarget = puzzle(30, "The Metameta", true);
    const g = group(30, [feederMetaA, feederMetaB, ownTarget]);
    expect(groupTarget(g)).toBe(ownTarget);
    expect(groupTarget(g)?.name).toBe("The Metameta");
  });

  it("picks the group's own target when it sorts FIRST among several targets", () => {
    // The mirror of the case above, so a fix that merely took the LAST is_meta
    // member (or reversed the scan) would fail here.
    const ownTarget = puzzle(10, "The Meta", true);
    const foreignTarget = puzzle(20, "Some Other Meta", true);
    const g = group(10, [ownTarget, foreignTarget]);
    expect(groupTarget(g)).toBe(ownTarget);
    expect(groupTarget(g)?.name).toBe("The Meta");
  });

  it("finds an ordinary meta group's target among plain feeders", () => {
    const target = puzzle(7, "Meta", true);
    const g = group(7, [target, puzzle(8, "Feeder A"), puzzle(9, "Feeder B")]);
    expect(groupTarget(g)).toBe(target);
  });

  it("returns null for a targetless holding-pen group", () => {
    // A TagGroup with no target is legitimate (a view / Unassigned Feeders).
    // Its members may still be other groups' targets, which must not be
    // mistaken for a target of this group.
    const g = group(null, [puzzle(10, "Round One Meta", true), puzzle(11, "Loose Puzzle")]);
    expect(groupTarget(g)).toBeNull();
  });

  it("returns null when the target id names a puzzle not in the group", () => {
    // The target can be absent from `puzzles` (a filtered/partial board);
    // that must not fall back to some other member.
    const g = group(99, [puzzle(10, "Round One Meta", true), puzzle(11, "Feeder")]);
    expect(groupTarget(g)).toBeNull();
  });

  it("returns null for an empty group", () => {
    expect(groupTarget(group(30, []))).toBeNull();
  });
});

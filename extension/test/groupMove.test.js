import { describe, it, expect } from "vitest";
import { groupMove } from "../src/lib/groupMove.js";

const tab = (o) => ({ groupId: -1, windowId: 1, index: 0, pinned: false, ...o });

describe("groupMove", () => {
  it("null when the new tab is ungrouped", () => {
    expect(groupMove(tab({}), tab({ groupId: 5 }))).toBeNull();
  });
  it("null when both are in the same group", () => {
    expect(groupMove(tab({ groupId: 5 }), tab({ groupId: 5 }))).toBeNull();
  });
  it("moves an ungrouped existing tab to the new tab's group and index", () => {
    expect(groupMove(tab({ groupId: 7, index: 3 }), tab({ index: 9 }))).toEqual({
      groupId: 7, windowId: 1, index: 3, crossWindow: false,
    });
  });
  it("moves a tab from group B to group A", () => {
    const m = groupMove(tab({ groupId: 7, index: 2 }), tab({ groupId: 8 }));
    expect(m.groupId).toBe(7);
    expect(m.index).toBe(2);
  });
  it("null when the existing tab is pinned", () => {
    expect(groupMove(tab({ groupId: 7 }), tab({ pinned: true }))).toBeNull();
  });
  it("flags a cross-window move with the new tab's window", () => {
    const m = groupMove(tab({ groupId: 7, windowId: 2 }), tab({ windowId: 1 }));
    expect(m).toMatchObject({ windowId: 2, crossWindow: true });
  });
});

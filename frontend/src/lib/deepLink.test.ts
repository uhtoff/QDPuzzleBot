import { describe, expect, it } from "vitest";
import { boardLink, drawerSearch, isOwnAddress, parseBoardLink } from "./deepLink";

describe("parseBoardLink", () => {
  it("reads puzzle and image", () => {
    expect(parseBoardLink("?puzzle=5&image=9")).toEqual({ puzzleId: 5, mediaId: 9 });
  });
  it("ignores non-integers", () => {
    expect(parseBoardLink("?puzzle=x&image=1.5")).toEqual({ puzzleId: null, mediaId: null });
  });
  it("handles empty", () => {
    expect(parseBoardLink("")).toEqual({ puzzleId: null, mediaId: null });
  });
});

describe("boardLink", () => {
  it("builds with only image", () => {
    expect(boardLink("abc", { mediaId: 9 })).toBe("/hunts/abc?image=9");
  });
  it("builds with both", () => {
    expect(boardLink("abc", { puzzleId: 5, mediaId: 9 })).toBe("/hunts/abc?puzzle=5&image=9");
  });
  it("builds with neither", () => {
    expect(boardLink("abc", {})).toBe("/hunts/abc");
  });
});

describe("drawerSearch (the board's address follows the open drawer)", () => {
  it("opening a drawer puts its puzzle in the address", () => {
    expect(drawerSearch("", 5)).toBe("puzzle=5");
  });
  it("another puzzle replaces it, dropping a consumed image", () => {
    expect(drawerSearch("puzzle=5", 6)).toBe("puzzle=6");
    expect(drawerSearch("puzzle=5&image=9", 5)).toBe("puzzle=5");
  });
  it("closing the drawer clears the address", () => {
    expect(drawerSearch("puzzle=5", null)).toBe("");
  });
  it("answers null when the address is already right", () => {
    expect(drawerSearch("puzzle=5", 5)).toBeNull();
    expect(drawerSearch("", null)).toBeNull();
  });
});

describe("isOwnAddress (the board ignores what it wrote itself)", () => {
  it("is the address the board last wrote", () => {
    expect(isOwnAddress("?puzzle=5", "puzzle=5")).toBe(true);
    expect(isOwnAddress("", "")).toBe(true);
  });
  it("anything else is a link to act on", () => {
    expect(isOwnAddress("puzzle=5&image=9", "puzzle=5")).toBe(false);
    expect(isOwnAddress("puzzle=6", "puzzle=5")).toBe(false);
    expect(isOwnAddress("puzzle=5", "")).toBe(false);
  });
  it("closing a drawer: the stale ?puzzle= in that render is not a link, so it can't reopen", () => {
    // Open puzzle 5: the board writes puzzle=5.
    let written = drawerSearch("", 5)!;
    // Close it: in that render the address still says puzzle=5 and no drawer is open.
    const stale = "puzzle=5";
    expect(isOwnAddress(stale, written)).toBe(true);   // ignored, not reopened
    // The board then writes the bare address.
    written = drawerSearch(stale, null)!;
    expect(written).toBe("");
    // A real link for the same puzzle afterwards still opens it.
    expect(isOwnAddress("puzzle=5", written)).toBe(false);
  });
});

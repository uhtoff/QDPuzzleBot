import { describe, expect, it } from "vitest";
import { boardLink, parseBoardLink } from "./deepLink";

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

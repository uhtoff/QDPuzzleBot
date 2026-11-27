import { describe, expect, it } from "vitest";
import type { EvidenceItem, PuzzleImage } from "../types";
import { resolveDeepLink, stripItems } from "./stripItems";

function own(id: number, mediaId: number, position: number): PuzzleImage {
  return { id, media_id: mediaId, media_url: `/m/${mediaId}`, source_region_id: null, position };
}

function ev(
  id: number,
  sourcePuzzleId: number | null,
  mediaId: number | null,
  createdAt: string,
): EvidenceItem {
  return {
    id,
    source_puzzle_id: sourcePuzzleId,
    source_puzzle_name: null,
    source_sheet_id: null,
    source_sheet_name: null,
    source_region_id: null,
    source_region_name: null,
    target_puzzle_id: null,
    target_puzzle_name: null,
    note: `n${id}`,
    media_id: mediaId,
    media_url: mediaId == null ? null : `/m/${mediaId}`,
    created_at: createdAt,
  };
}

describe("stripItems", () => {
  const items = stripItems(
    [own(1, 101, 2), own(2, 102, 0), own(3, 103, 1)],
    [
      ev(10, 7, 201, "2026-01-02T00:00:00Z"),
      ev(11, 8, 202, "2026-01-01T00:00:00Z"),
      ev(12, 7, null, "2026-01-01T00:00:00Z"),
      ev(13, 7, 203, "2026-01-01T00:00:00Z"),
    ],
    7,
  );

  it("lists own images by position, then this puzzle's media evidence oldest first", () => {
    expect(
      items.map((i) => (i.kind === "own" ? `own${i.image.id}` : `ev${i.item.id}`)),
    ).toEqual(["own2", "own3", "own1", "ev13", "ev10"]);
  });
});

describe("resolveDeepLink", () => {
  const list = stripItems([own(1, 101, 0)], [ev(10, 7, 201, "2026-01-01T00:00:00Z")], 7);
  const puzzles = [{ id: 7 }];

  it("has no notice when the image is an own image or evidence", () => {
    expect(resolveDeepLink({ puzzleId: 7, mediaId: 101 }, puzzles, list)).toEqual({
      puzzleId: 7, mediaId: 101, notice: null,
    });
    expect(resolveDeepLink({ puzzleId: 7, mediaId: 201 }, puzzles, list)).toEqual({
      puzzleId: 7, mediaId: 201, notice: null,
    });
  });

  it("keeps the puzzle but warns when the image is gone", () => {
    expect(resolveDeepLink({ puzzleId: 7, mediaId: 999 }, puzzles, list)).toEqual({
      puzzleId: 7, mediaId: null, notice: "That image is no longer on this puzzle",
    });
  });

  it("opens the puzzle with no notice when only a puzzle is given", () => {
    expect(resolveDeepLink({ puzzleId: 7, mediaId: null }, puzzles, list)).toEqual({
      puzzleId: 7, mediaId: null, notice: null,
    });
  });

  it("nulls everything and warns for an unknown puzzle", () => {
    const r = resolveDeepLink({ puzzleId: 99, mediaId: 101 }, puzzles, list);
    expect(r.puzzleId).toBeNull();
    expect(r.mediaId).toBeNull();
    expect(r.notice).toBe("That puzzle no longer exists");
  });
});

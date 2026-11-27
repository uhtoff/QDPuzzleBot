/** What a puzzle's image strip shows, and how a deep link resolves against it. */
import type { EvidenceItem, PuzzleImage } from "../types";

export type StripItem =
  | { kind: "own"; image: PuzzleImage }
  | { kind: "evidence"; item: EvidenceItem };

/** Own images by position, then evidence that came FROM this puzzle and has an
 *  image, oldest first. */
export function stripItems(
  own: PuzzleImage[],
  evidence: EvidenceItem[],
  puzzleId: number,
): StripItem[] {
  const ownItems: StripItem[] = [...own]
    .sort((a, b) => a.position - b.position)
    .map((image) => ({ kind: "own", image }));
  const evItems: StripItem[] = evidence
    .filter((e) => e.source_puzzle_id === puzzleId && e.media_id != null)
    .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""))
    .map((item) => ({ kind: "evidence", item }));
  return [...ownItems, ...evItems];
}

export function stripItemMediaId(item: StripItem): number | null {
  return item.kind === "own" ? item.image.media_id : item.item.media_id;
}

export function resolveDeepLink(
  link: { puzzleId: number | null; mediaId: number | null },
  puzzles: { id: number }[],
  items: StripItem[],
): { puzzleId: number | null; mediaId: number | null; notice: string | null } {
  if (link.puzzleId == null || !puzzles.some((p) => p.id === link.puzzleId)) {
    return { puzzleId: null, mediaId: null, notice: "That puzzle no longer exists" };
  }
  if (link.mediaId == null) {
    return { puzzleId: link.puzzleId, mediaId: null, notice: null };
  }
  if (items.some((i) => stripItemMediaId(i) === link.mediaId)) {
    return { puzzleId: link.puzzleId, mediaId: link.mediaId, notice: null };
  }
  return {
    puzzleId: link.puzzleId,
    mediaId: null,
    notice: "That image is no longer on this puzzle",
  };
}

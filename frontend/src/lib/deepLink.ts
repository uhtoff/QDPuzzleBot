/** Deep links into a hunt board: `/hunts/<uid>?puzzle=<id>&image=<id>`. */

function parseId(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) return null;
  return Number(value);
}

export function parseBoardLink(search: string): {
  puzzleId: number | null;
  mediaId: number | null;
} {
  const params = new URLSearchParams(search);
  return { puzzleId: parseId(params.get("puzzle")), mediaId: parseId(params.get("image")) };
}

export function boardLink(
  uid: string,
  opts: { puzzleId?: number; mediaId?: number },
): string {
  const params: string[] = [];
  if (opts.puzzleId !== undefined) params.push(`puzzle=${opts.puzzleId}`);
  if (opts.mediaId !== undefined) params.push(`image=${opts.mediaId}`);
  return `/hunts/${uid}${params.length ? "?" + params.join("&") : ""}`;
}

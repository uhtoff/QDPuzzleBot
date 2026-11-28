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

/** The board's address while a puzzle's drawer is open is `?puzzle=<id>`, and
 *  bare when none is, so the chat extension can show that puzzle's chat. The
 *  new search (no "?") when the address should change, or null when it is
 *  already right. A deep link's `image` is dropped once it has been opened. */
export function drawerSearch(current: string, openPuzzleId: number | null): string | null {
  const want = openPuzzleId == null ? "" : `puzzle=${openPuzzleId}`;
  return new URLSearchParams(current).toString() === want ? null : want;
}

/** True when `search` is exactly what the board itself last wrote to the
 *  address (`written`), so it is not a deep link to act on. Compared against
 *  what was written, not the open drawer: in the render that closes a drawer
 *  the address still holds its ?puzzle= until the board rewrites it, and
 *  reading that as a link would reopen the drawer. */
export function isOwnAddress(search: string, written: string): boolean {
  return new URLSearchParams(search).toString() === new URLSearchParams(written).toString();
}

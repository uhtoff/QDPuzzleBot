export interface Box {
  top: number;
  bottom: number;
  left: number;
  width: number;
}

const GAP = 6;
const MARGIN = 4;

/** Where to put a tooltip of `tip` size for an `anchor` box, in viewport
 *  coordinates: centred above the anchor, flipped below when there is no room
 *  above, and kept inside the viewport horizontally. */
export function tipPosition(
  anchor: Box,
  tip: { width: number; height: number },
  viewportWidth: number,
): { left: number; top: number } {
  const centred = anchor.left + anchor.width / 2 - tip.width / 2;
  const maxLeft = Math.max(MARGIN, viewportWidth - tip.width - MARGIN);
  const left = Math.min(Math.max(centred, MARGIN), maxLeft);
  const above = anchor.top - GAP - tip.height;
  const top = above >= MARGIN ? above : anchor.bottom + GAP;
  return { left, top };
}

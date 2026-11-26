import type { CanvasRegion, RegionShape } from "../types";

/** A freshly drawn shape and the region it was saved as. */
export interface SavedShape {
  shape: RegionShape;
  region: CanvasRegion;
}

/** The region the modal is editing: the opened region, else the region a freshly
 *  drawn shape was already saved as. "Create and link" saves the shape before it
 *  creates the puzzle; when that create fails or is cancelled the modal stays on
 *  the same shape, so every later save must reuse this region, never save the
 *  shape again. Matched by identity: the shape object stays the same while the
 *  modal is open on it. */
export function existingRegion(
  region: CanvasRegion | null,
  pendingShape: RegionShape | null,
  saved: SavedShape | null,
): CanvasRegion | null {
  if (region) return region;
  if (pendingShape && saved?.shape === pendingShape) return saved.region;
  return null;
}

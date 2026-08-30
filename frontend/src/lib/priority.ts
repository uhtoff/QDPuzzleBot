/**
 * Ordering and badge colour for a stored priority value.
 *
 * Lives here rather than in util.ts so it is unit-testable without Mantine —
 * the same split statusTone and statusLabel already use; util.ts re-exports.
 *
 * The vocabulary is bot/core/constants.py's PRIORITIES — ["low", "medium",
 * "high", "very high"] — stored canonically lowercase since migration 041, and
 * offered verbatim by the dashboard picker (PuzzleEditCells.PRIORITY_OPTIONS).
 * Every one of the four is listed here EXPLICITLY. The table used to hold only
 * {high, low} and let the rest fall through a `?? medium` default, which
 * silently collapsed "very high" onto "medium": the one value whose whole point
 * is to outrank "high" sorted below it, and rendered in the same calm blue.
 * A default is the wrong home for a value that is in the vocabulary.
 */
const PRIORITY_RANK: Record<string, number> = {
  "very high": 0,
  high: 1,
  medium: 2,
  low: 3,
};

// Where a value outside PRIORITIES lands. Kept deliberately, and kept equal to
// "medium": rows predating migration 041 can hold spellings this table does not
// know (creation wrote "Normal" for years), and a stranger belongs in the middle
// rather than jumping the queue or sinking below "low".
const UNKNOWN_PRIORITY_RANK = PRIORITY_RANK.medium;

// Behind every set priority, "low" included — no priority is not a low one.
const UNSET_PRIORITY_RANK = 4;

/** Sort key for a stored priority: most urgent first, unset last. */
export function priorityRank(priority: string | null): number {
  if (!priority) return UNSET_PRIORITY_RANK;
  return PRIORITY_RANK[priority.toLowerCase()] ?? UNKNOWN_PRIORITY_RANK;
}

const PRIORITY_COLOR: Record<string, string> = {
  // "very high" shares the red band with "high": both are urgent, and the badge
  // label already distinguishes them. What it must never do is read as calm.
  "very high": "red",
  high: "red",
  medium: "blue",
  low: "gray",
};

// Same reasoning as UNKNOWN_PRIORITY_RANK: an unrecognised or unset priority
// renders as medium.
const UNKNOWN_PRIORITY_COLOR = PRIORITY_COLOR.medium;

/** Mantine badge colour for a stored priority. */
export function priorityColor(priority: string | null): string {
  if (!priority) return UNKNOWN_PRIORITY_COLOR;
  return PRIORITY_COLOR[priority.toLowerCase()] ?? UNKNOWN_PRIORITY_COLOR;
}

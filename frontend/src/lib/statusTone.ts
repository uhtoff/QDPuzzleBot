export type StatusTone = "solved" | "stuck" | "working" | "unstarted" | "unknown";

/**
 * One set of status rules, shared by util.statusColor (Mantine colour names,
 * for badges) and CanvasView.tintFor (hex, for region tints) -- these had
 * re-diverged: the tint treated "needs ..." as work in flight, the badge did
 * not. This is the single rule set; each caller maps a tone to its own
 * palette.
 */
export function statusTone(status: string | null): StatusTone {
  // Matched case-insensitively on a SUBSTRING because a status's spelling
  // depends on where it was set: the bot writes constants.py's lowercase
  // values ("in progress"), while the dashboard's own dropdown writes title
  // case ("In Progress"). An exact lowercase switch silently greyed out every
  // status set from the web.
  const s = (status || "").toLowerCase();
  // "unsolved" is a real stored status and CONTAINS "solved", so it has to be
  // excluded or an unsolved puzzle gets a green badge/tint. "backsolved" must
  // still match.
  if (s.includes("solved") && !s.includes("unsolved")) return "solved";
  if (s.includes("stuck")) return "stuck";
  if (s.includes("progress") || s.includes("working") || s.includes("needs")) return "working";
  if (s.includes("unstarted")) return "unstarted";
  return "unknown";
}

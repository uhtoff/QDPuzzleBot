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
  // Two stored statuses CONTAIN "solved" without being solved, and both have to
  // be excluded here or they get a green badge/tint: "unsolved" (a twice-fixed
  // regression) and "partially solved", which means work remains and so reads as
  // in-flight, alongside "needs ...". "backsolved" must still match.
  if (s.includes("solved") && !s.includes("unsolved") && !s.includes("partially")) return "solved";
  if (s.includes("stuck")) return "stuck";
  if (s.includes("progress") || s.includes("working") || s.includes("needs")
      || s.includes("partial")) return "working";
  if (s.includes("unstarted")) return "unstarted";
  return "unknown";
}

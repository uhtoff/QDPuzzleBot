// The hunt board's view switcher entries, in display order, each with the width
// it needs in the desktop SegmentedControl (the control clips without an
// explicit width, so every entry carries its own allowance).

export type BoardView = "board" | "all" | "evidence" | "canvas" | "arrange" | "manage";

export interface BoardViewOption {
  label: string;
  value: BoardView;
  width: number;
}

export function boardViews(opts: { canManage: boolean; canAdminister: boolean }): BoardViewOption[] {
  const views: BoardViewOption[] = [
    { label: "By group", value: "board", width: 120 },
    { label: "All puzzles", value: "all", width: 120 },
    { label: "Evidence", value: "evidence", width: 120 },
    { label: "Canvas", value: "canvas", width: 90 },
  ];
  if (opts.canManage) views.push({ label: "Arrange", value: "arrange", width: 105 });
  // Admins can always reach Manage (to unarchive) even when the hunt is
  // archived and can_manage is therefore false.
  if (opts.canManage || opts.canAdminister) views.push({ label: "Manage", value: "manage", width: 105 });
  return views;
}

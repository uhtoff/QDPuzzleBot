/** The hunt board's badge for a finished team of a multi-team hunt. */

export interface MultiTeamStatus {
  name: string;
  completed: boolean;
  archived: boolean;
  team: string | null;
  /** This team's finishing place, null until it has finished. */
  place: number | null;
  team_count: number;
}

export function ordinal(n: number): string {
  if (n % 100 >= 10 && n % 100 <= 20) return `${n}th`;
  return `${n}${({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th"}`;
}

/** "Complete · 3rd of 5" for a finished team, "Complete" if its place is
 *  unknown, or null (no badge) for a team still playing or a single-team hunt. */
export function completeLabel(mt: MultiTeamStatus | null): string | null {
  if (!mt?.completed) return null;
  return mt.place ? `Complete · ${ordinal(mt.place)} of ${mt.team_count}` : "Complete";
}

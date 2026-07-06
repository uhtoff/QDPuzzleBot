import { notifications } from "@mantine/notifications";
import type { BoardGroup, Puzzle } from "./types";

/**
 * How many puzzles a full "delete round + all its puzzles" actually deletes.
 *
 * Mirrors the backend delete predicate (puzzle_service.delete_round with
 * keep_puzzles=False): only the round's OWN target puzzle plus non-target
 * feeders tagged EXCLUSIVELY to this round are deleted. Shared feeders (also
 * tagged in another round) and puzzles that are some other round's target are
 * kept and relocated — so `group.puzzles.length` over-counts the warning.
 */
export function fullDeletePuzzleCount(group: BoardGroup): number {
  return group.puzzles.filter((p) => deletedByFullDelete(p, group)).length;
}

function deletedByFullDelete(p: Puzzle, group: BoardGroup): boolean {
  // The round's own target puzzle is always deleted.
  if (p.id === group.target_puzzle_id) return true;
  // A puzzle that is some round's target (is_meta) is kept and relocated.
  if (p.is_meta) return false;
  // A plain feeder is deleted only if it is tagged to no other round.
  return p.rounds.every((r) => r.id === group.tag_group_id);
}

export function notifyError(e: unknown): void {
  const message = e instanceof Error ? e.message : String(e);
  notifications.show({ message, color: "red" });
}

export function notifySuccess(msg: string): void {
  notifications.show({ message: msg, color: "green" });
}

export function showSheetWarning(data: unknown): void {
  if (
    data &&
    typeof data === "object" &&
    "sheet_warning" in data &&
    (data as Record<string, unknown>).sheet_warning
  ) {
    notifications.show({
      message: String((data as Record<string, unknown>).sheet_warning),
      color: "yellow",
      autoClose: 8000,
    });
  }
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const mins = Math.round(diffSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** The browser's current UTC offset in minutes east of UTC (e.g. UTC+2 -> 120). */
export function browserOffsetMinutes(): number {
  return -new Date().getTimezoneOffset();
}

/** Format a UTC offset in minutes as "UTC+02:00" / "UTC-05:30". */
function formatOffset(minutes: number): string {
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  const hh = String(Math.floor(abs / 60)).padStart(2, "0");
  const mm = String(abs % 60).padStart(2, "0");
  return `UTC${sign}${hh}:${mm}`;
}

/** Select options for a UTC-offset picker: UTC-12:00 .. UTC+14:00, half-hour steps. */
export const UTC_OFFSET_OPTIONS = Array.from({ length: 53 }, (_, i) => {
  const minutes = -720 + i * 30;
  return { value: String(minutes), label: formatOffset(minutes) };
});

/** Render a stored UTC timestamp as a datetime-local wall-clock string in `offsetMinutes`. */
export function tsToLocal(ts: number | null, offsetMinutes: number): string {
  if (ts == null || ts <= 0) return "";
  return new Date(ts * 1000 + offsetMinutes * 60000).toISOString().slice(0, 16);
}

/** Parse a datetime-local wall-clock string (expressed in offsetMinutes) to a UTC timestamp. */
export function localToTs(local: string, offsetMinutes: number): number | null {
  const [datePart, timePart] = local.split("T");
  if (!datePart || !timePart) return null;
  const [y, m, d] = datePart.split("-").map(Number);
  const [hh, mm] = timePart.split(":").map(Number);
  return Math.round((Date.UTC(y, m - 1, d, hh, mm) - offsetMinutes * 60000) / 1000);
}

/** Length of an answer counting only letters and digits (no spaces or punctuation). */
export function answerLength(solution: string | null): number {
  if (!solution) return 0;
  return (solution.match(/[\p{L}\p{N}]/gu) ?? []).length;
}

export function priorityColor(priority: string | null): string {
  switch ((priority || "").toLowerCase()) {
    case "high":
      return "red";
    case "low":
      return "gray";
    default:
      return "blue";
  }
}

export function statusColor(status: string | null): string {
  const s = (status || "").toLowerCase();
  if (s.includes("solved")) return "green";
  if (s.includes("stuck")) return "red";
  if (s.includes("progress") || s.includes("working")) return "yellow";
  if (s.includes("unstarted")) return "gray";
  return "blue";
}

const PRIORITY_RANK: Record<string, number> = { high: 0, low: 2 };
const META_KIND_RANK: Record<string, number> = {
  runaround: 0,
  metameta: 1,
  meta: 2,
  capstone: 3,
};

function priorityRank(p: Puzzle): number {
  if (!p.priority) return 3;
  return PRIORITY_RANK[p.priority.toLowerCase()] ?? 1;
}

function metaKindRank(p: Puzzle): number {
  if (!p.meta_kind) return 4;
  return META_KIND_RANK[p.meta_kind] ?? 4;
}

export function sortPuzzles(puzzles: Puzzle[], groupCode?: string | null): Puzzle[] {
  return [...puzzles].sort((a, b) => {
    if (groupCode != null) {
      const aT = a.rounds.some((r) => r.group_code === groupCode && r.is_target_of);
      const bT = b.rounds.some((r) => r.group_code === groupCode && r.is_target_of);
      if (aT !== bT) return aT ? -1 : 1;
    }
    if (a.solved !== b.solved) return a.solved ? 1 : -1;
    const pd = priorityRank(a) - priorityRank(b);
    if (pd !== 0) return pd;
    const md = metaKindRank(a) - metaKindRank(b);
    if (md !== 0) return md;
    return a.id - b.id;
  });
}

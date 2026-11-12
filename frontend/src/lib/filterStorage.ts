// Remembers a hunt's board filter across reloads, per browser. The search box
// is deliberately not kept: finding a page that hides most puzzles behind a
// half-forgotten search reads as missing data. Status, priority, type and
// "Hide solved" are settings people choose once and expect to stay.
import { EMPTY_FILTER, type PuzzleFilter } from "./puzzleFilter";

const key = (uid: string) => `puzzbot.boardFilter.${uid}`;

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

/** A stored value back into a filter; anything unreadable is the empty filter. */
export function parseStoredFilter(raw: string | null): PuzzleFilter {
  if (!raw) return EMPTY_FILTER;
  try {
    const o: unknown = JSON.parse(raw);
    if (!o || typeof o !== "object") return EMPTY_FILTER;
    const r = o as Record<string, unknown>;
    return {
      search: "",
      statuses: strings(r.statuses),
      priorities: strings(r.priorities),
      types: strings(r.types),
      hideSolved: r.hideSolved === true,
    };
  } catch {
    return EMPTY_FILTER;
  }
}

export function serialiseFilter(f: PuzzleFilter): string {
  return JSON.stringify({ statuses: f.statuses, priorities: f.priorities, types: f.types, hideSolved: f.hideSolved });
}

// Storage can be missing or throw (private windows, blocked site data); the
// board must work regardless, so every access is guarded.
export function loadFilter(uid: string | undefined): PuzzleFilter {
  if (!uid) return EMPTY_FILTER;
  try {
    return parseStoredFilter(window.localStorage.getItem(key(uid)));
  } catch {
    return EMPTY_FILTER;
  }
}

export function saveFilter(uid: string | undefined, f: PuzzleFilter): void {
  if (!uid) return;
  try {
    window.localStorage.setItem(key(uid), serialiseFilter(f));
  } catch {
    /* not kept; nothing else depends on it */
  }
}

// Remembers which By-group panels a person opened or collapsed, per hunt, per
// browser. Stored as differences from the default (unsolved groups open,
// solved ones closed) rather than as a list of open groups, so a group
// created later still opens by default, and one whose meta gets solved still
// closes unless the person chose otherwise.

export interface CollapseOverrides {
  /** Groups opened although they are closed by default. */
  opened: string[];
  /** Groups closed although they are open by default. */
  closed: string[];
}

export const NO_OVERRIDES: CollapseOverrides = { opened: [], closed: [] };

/** The open groups among `visible`: the default, adjusted by the overrides. */
export function applyOverrides(
  defaultOpen: string[],
  visible: string[],
  o: CollapseOverrides,
): string[] {
  const def = new Set(defaultOpen);
  const opened = new Set(o.opened);
  const closed = new Set(o.closed);
  return visible.filter((id) => (def.has(id) || opened.has(id)) && !closed.has(id));
}

/** The overrides after the person changed the open set to `nextOpen`. Only
 *  `visible` groups are re-derived; overrides for groups not on screen right
 *  now (filtered out, or empty) are kept as they were. */
export function deriveOverrides(
  prev: CollapseOverrides,
  defaultOpen: string[],
  visible: string[],
  nextOpen: string[],
): CollapseOverrides {
  const def = new Set(defaultOpen);
  const open = new Set(nextOpen);
  const seen = new Set(visible);
  return {
    opened: [
      ...prev.opened.filter((id) => !seen.has(id)),
      ...visible.filter((id) => open.has(id) && !def.has(id)),
    ],
    closed: [
      ...prev.closed.filter((id) => !seen.has(id)),
      ...visible.filter((id) => !open.has(id) && def.has(id)),
    ],
  };
}

const key = (uid: string) => `puzzbot.boardCollapse.${uid}`;

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

/** A stored value back into overrides; anything unreadable is "no overrides". */
export function parseStoredCollapse(raw: string | null): CollapseOverrides {
  if (!raw) return NO_OVERRIDES;
  try {
    const o: unknown = JSON.parse(raw);
    if (!o || typeof o !== "object") return NO_OVERRIDES;
    const r = o as Record<string, unknown>;
    return { opened: strings(r.opened), closed: strings(r.closed) };
  } catch {
    return NO_OVERRIDES;
  }
}

// Storage can be missing or throw (private windows, blocked site data); the
// board must work regardless, so every access is guarded.
export function loadCollapse(uid: string | undefined): CollapseOverrides {
  if (!uid) return NO_OVERRIDES;
  try {
    return parseStoredCollapse(window.localStorage.getItem(key(uid)));
  } catch {
    return NO_OVERRIDES;
  }
}

export function saveCollapse(uid: string | undefined, o: CollapseOverrides): void {
  if (!uid) return;
  try {
    window.localStorage.setItem(key(uid), JSON.stringify({ opened: o.opened, closed: o.closed }));
  } catch {
    /* not kept; nothing else depends on it */
  }
}

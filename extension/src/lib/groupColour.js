// Chrome tab group colours, minus "grey" (it reads as an inactive group).
export const GROUP_COLOURS = ["blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"];

// Stable colour for a round name: FNV-1a 32-bit hash of the trimmed, lower-cased name.
export function groupColour(name) {
  const s = String(name ?? "").trim().toLowerCase();
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return GROUP_COLOURS[h % GROUP_COLOURS.length];
}

// Colour for a new round group. `remembered` wins; else the hash colour if no
// open group uses it; else the next unused colour from there (wrapping); else
// the least-used (ties: earliest from the hash position).
export function pickColour(name, usedColours, remembered) {
  if (remembered && GROUP_COLOURS.includes(remembered)) return remembered;
  const start = GROUP_COLOURS.indexOf(groupColour(name));
  const order = GROUP_COLOURS.map((_, i) => GROUP_COLOURS[(start + i) % GROUP_COLOURS.length]);
  const count = (c) => usedColours.filter((u) => u === c).length;
  const free = order.find((c) => count(c) === 0);
  if (free) return free;
  let best = order[0];
  for (const c of order) if (count(c) < count(best)) best = c;
  return best;
}

// An open group whose title matches the round name (trimmed, case-insensitive).
export function findRoundGroup(name, groups) {
  const want = String(name ?? "").trim().toLowerCase();
  if (want === "") return null;
  return groups.find((g) => String(g.title ?? "").trim().toLowerCase() === want) ?? null;
}

// The open group for one puzzle: its title matches the puzzle name AND it holds that
// puzzle's hunt page (remembered when Open puzzle or the Puzzle page link opened it). A title
// alone is not enough (a round is often named like its meta, other hunts reuse names), nor is
// holding the puzzle's sheet: the round's own group holds its meta's sheet. Only a puzzle
// group ever gets a page. `tabsOf` maps a candidate group's id to its known tabs'
// {spreadsheetId, gid, page}.
export function findPuzzleGroup(name, groups, tabsOf, want) {
  const title = String(name ?? "").trim().toLowerCase();
  if (title === "" || !want) return null;
  const isThisPage = (s) =>
    !!s && s.page === true && s.spreadsheetId === want.spreadsheetId && (s.gid ?? null) === (want.gid ?? null);
  return (
    groups.find((g) => String(g.title ?? "").trim().toLowerCase() === title && (tabsOf.get(g.id) ?? []).some(isThisPage)) ??
    null
  );
}

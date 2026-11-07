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
  return groups.find((g) => String(g.title ?? "").trim().toLowerCase() === want) ?? null;
}

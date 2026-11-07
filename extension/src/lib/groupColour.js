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

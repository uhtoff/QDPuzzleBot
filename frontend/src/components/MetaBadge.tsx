import { Badge } from "@mantine/core";
import type { Puzzle } from "../types";

// Single label per target puzzle, by precedence Runaround > Metameta > Meta >
// Capstone. meta_kind is derived server-side (bot/web/data.py derive_meta_kind).
// minimeta is a separate derived flag (derive_is_minimeta) — minimetas are not
// is_meta, so they never carry a meta_kind.
export const KINDS: Record<string, { label: string; color: string }> = {
  runaround: { label: "RUNAROUND", color: "red" },
  metameta: { label: "METAMETA", color: "violet" },
  capstone: { label: "CAPSTONE", color: "orange" },
  meta: { label: "META", color: "grape" },
  minimeta: { label: "MINIMETA", color: "grape.4" },
};

// The kind a puzzle carries (for the badge / Arrange kind-line), or null for
// a plain feeder that isn't a target or minimeta.
export function getKind(puzzle: Puzzle): { label: string; color: string } | null {
  if (!puzzle.is_meta) {
    return puzzle.is_minimeta ? KINDS.minimeta : null;
  }
  return KINDS[puzzle.meta_kind ?? "meta"] ?? KINDS.meta;
}

// A KINDS color ("grape" or "grape.4") as a Mantine CSS variable, for use in
// inline styles (e.g. a border) where the color style prop isn't available.
export function kindColorVar(color: string): string {
  const [name, shade] = color.split(".");
  return `var(--mantine-color-${name}-${shade ?? 6})`;
}

export function MetaBadge({ puzzle }: { puzzle: Puzzle }) {
  const kind = getKind(puzzle);
  if (!kind) return null;
  return (
    <Badge size="xs" color={kind.color}>
      {kind.label}
    </Badge>
  );
}

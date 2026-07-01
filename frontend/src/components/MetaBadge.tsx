import { Badge } from "@mantine/core";
import type { Puzzle } from "../types";

// Single label per target puzzle, by precedence Runaround > Metameta > Meta >
// Capstone. meta_kind is derived server-side (bot/web/data.py derive_meta_kind).
// minimeta is a separate derived flag (derive_is_minimeta) — minimetas are not
// is_meta, so they never carry a meta_kind.
const KINDS: Record<string, { label: string; color: string }> = {
  runaround: { label: "RUNAROUND", color: "red" },
  metameta: { label: "METAMETA", color: "violet" },
  capstone: { label: "CAPSTONE", color: "orange" },
  meta: { label: "META", color: "grape" },
  minimeta: { label: "MINIMETA", color: "grape.4" },
};

export function MetaBadge({ puzzle }: { puzzle: Puzzle }) {
  if (!puzzle.is_meta) {
    if (puzzle.is_minimeta) {
      const kind = KINDS.minimeta;
      return (
        <Badge size="xs" color={kind.color}>
          {kind.label}
        </Badge>
      );
    }
    return null;
  }
  const kind = KINDS[puzzle.meta_kind ?? "meta"] ?? KINDS.meta;
  return (
    <Badge size="xs" color={kind.color}>
      {kind.label}
    </Badge>
  );
}

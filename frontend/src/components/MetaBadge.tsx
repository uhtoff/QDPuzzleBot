import { Badge } from "@mantine/core";
import type { Puzzle } from "../types";

// Single label per target puzzle, by precedence Runaround > Metameta > Meta >
// Capstone. meta_kind is derived server-side (bot/web/data.py derive_meta_kind).
const KINDS: Record<string, { label: string; color: string }> = {
  runaround: { label: "RUNAROUND", color: "red" },
  metameta: { label: "METAMETA", color: "violet" },
  capstone: { label: "CAPSTONE", color: "orange" },
  meta: { label: "META", color: "grape" },
};

export function MetaBadge({ puzzle }: { puzzle: Puzzle }) {
  if (!puzzle.is_meta) return null;
  const kind = KINDS[puzzle.meta_kind ?? "meta"] ?? KINDS.meta;
  return (
    <Badge size="xs" color={kind.color}>
      {kind.label}
    </Badge>
  );
}

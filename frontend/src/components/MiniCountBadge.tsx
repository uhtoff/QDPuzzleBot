import { memo } from "react";
import { Badge } from "@mantine/core";
import type { Puzzle } from "../types";

/** Violet mini-solved count for a minimeta puzzle. Visual only — the row band
 *  handles expanding the mini list. */
export const MiniCountBadge = memo(function MiniCountBadge({ puzzle: p }: { puzzle: Puzzle }) {
  if (!p.is_minimeta) return null;
  const solved = p.mini_puzzles.filter((m) => m.solved).length;
  return (
    <Badge
      color="violet"
      variant="light"
      style={{ flexShrink: 0 }}
      data-tip={`${solved}/${p.mini_puzzles.length} minis solved`}
      aria-label="Minis solved"
    >
      {solved}/{p.mini_puzzles.length}
    </Badge>
  );
});

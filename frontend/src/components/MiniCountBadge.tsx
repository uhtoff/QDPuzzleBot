import { Badge, Tooltip } from "@mantine/core";
import type { Puzzle } from "../types";

/** Violet mini-solved count for a minimeta puzzle. Visual only — the row band
 *  handles expanding the mini list. */
export function MiniCountBadge({ puzzle: p }: { puzzle: Puzzle }) {
  if (!p.is_minimeta) return null;
  const solved = p.mini_puzzles.filter((m) => m.solved).length;
  return (
    <Tooltip label={`${solved}/${p.mini_puzzles.length} minis solved`}>
      <Badge color="violet" variant="light" style={{ flexShrink: 0 }} aria-label="Minis solved">
        {solved}/{p.mini_puzzles.length}
      </Badge>
    </Tooltip>
  );
}

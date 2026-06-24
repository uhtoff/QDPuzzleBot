import { ActionIcon, Badge, Group, Indicator, Table, Tooltip } from "@mantine/core";
import { IconCircleCheck, IconNote } from "@tabler/icons-react";
import type { Puzzle } from "../types";

interface PuzzleRowActionsProps {
  puzzle: Puzzle;
  editable: boolean;
  onSolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onToggleMinis: (id: number) => void;
  /** Width of the <Table.Td> wrapper. Defaults to undefined (auto). */
  tdWidth?: number;
}

/**
 * Shared row-actions cell for puzzle tables.
 *
 * Renders a single <Table.Td> containing the solve button, notes indicator,
 * and (for Minimeta puzzles) a mini-expansion toggle.
 *
 * Place this inside the puzzle's <Table.Tr>. The Minimeta expansion row
 * (<MiniPuzzlePanel>) should be rendered as a sibling <Table.Tr> after the
 * main row using the `expandedMinis` set supplied by `useMiniExpansion`.
 */
export function PuzzleRowActions({
  puzzle: p,
  editable,
  onSolve,
  onOpenNotes,
  onToggleMinis,
  tdWidth,
}: PuzzleRowActionsProps) {
  return (
    <Table.Td w={tdWidth}>
      <Group gap={4} wrap="nowrap">
        {editable && !p.solved && (
          <Tooltip label="Mark solved">
            <ActionIcon variant="subtle" color="green" onClick={() => onSolve(p)} aria-label="Solve">
              <IconCircleCheck size={17} />
            </ActionIcon>
          </Tooltip>
        )}
        <Indicator label={p.notes.length} size={16} disabled={p.notes.length === 0} color="blue">
          <ActionIcon variant="subtle" color="gray" onClick={() => onOpenNotes(p)} aria-label="Notes">
            <IconNote size={16} />
          </ActionIcon>
        </Indicator>
        {p.puzzle_type === "Minimeta" && (
          <Tooltip label={`${p.mini_puzzles.filter((m) => m.solved).length}/${p.mini_puzzles.length} minis solved`}>
            <ActionIcon
              size="sm"
              variant="subtle"
              onClick={() => onToggleMinis(p.id)}
              aria-label="Toggle minis"
            >
              <Badge color="violet" variant="light">
                {p.mini_puzzles.filter((m) => m.solved).length}/{p.mini_puzzles.length}
              </Badge>
            </ActionIcon>
          </Tooltip>
        )}
      </Group>
    </Table.Td>
  );
}

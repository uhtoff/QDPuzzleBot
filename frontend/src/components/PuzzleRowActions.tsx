import { ActionIcon, Badge, Group, Indicator, Table, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconCircleCheck, IconCircleX, IconNote } from "@tabler/icons-react";
import type { Puzzle } from "../types";

interface PuzzleRowActionsProps {
  puzzle: Puzzle;
  editable: boolean;
  onSolve: (puzzle: Puzzle) => void;
  onUnsolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  /** Width of the <Table.Td> wrapper. Defaults to undefined (auto). */
  tdWidth?: number;
}

/**
 * Shared row-actions cell for puzzle tables.
 *
 * Renders a single <Table.Td> containing the solve/unsolve button and notes indicator.
 *
 * Place this inside the puzzle's <Table.Tr>.
 */
export function PuzzleRowActions({
  puzzle: p,
  editable,
  onSolve,
  onUnsolve,
  onOpenNotes,
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
        {editable && p.solved && (
          <Tooltip label="Mark unsolved">
            <ActionIcon variant="subtle" color="red" onClick={() => onUnsolve(p)} aria-label="Unsolve">
              <IconCircleX size={17} />
            </ActionIcon>
          </Tooltip>
        )}
        <Indicator label={p.notes.length} size={16} disabled={p.notes.length === 0} color="blue">
          <ActionIcon variant="subtle" color="gray" onClick={() => onOpenNotes(p)} aria-label="Notes">
            <IconNote size={16} />
          </ActionIcon>
        </Indicator>
        {p.rollup_stale && (
          <Tooltip label={p.rollup_error || "This minimeta's sheet is out of date"} multiline maw={320}>
            <Badge color="red" variant="light" size="sm" leftSection={<IconAlertTriangle size={12} />} style={{ cursor: "default" }}>
              sheet stale
            </Badge>
          </Tooltip>
        )}
      </Group>
    </Table.Td>
  );
}

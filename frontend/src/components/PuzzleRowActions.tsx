import { memo } from "react";
import { ActionIcon, Badge, Group, Indicator, Table, Tooltip } from "@mantine/core";
import {
  IconAlertTriangle,
  IconBrowserPlus,
  IconCircleCheck,
  IconCircleX,
  IconNote,
  IconPlayerPlay,
} from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { extensionSupports, openPuzzle, workOn } from "../lib/extension";

interface PuzzleRowActionsProps {
  puzzle: Puzzle;
  editable: boolean;
  onSolve: (puzzle: Puzzle) => void;
  onUnsolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  /** Hunt uid; enables the extension's "Work on this puzzle" button. */
  huntUid?: string;
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
export const PuzzleRowActions = memo(function PuzzleRowActions({
  puzzle: p,
  editable,
  onSolve,
  onUnsolve,
  onOpenNotes,
  huntUid,
  tdWidth,
}: PuzzleRowActionsProps) {
  const sheetUrl = p.sheet_url;
  const puzzleUrl = p.url;
  return (
    <Table.Td w={tdWidth}>
      <Group gap={4} wrap="nowrap">
        {editable && !p.solved && (
          <ActionIcon
            variant="subtle"
            color="green"
            onClick={() => onSolve(p)}
            data-tip="Mark solved"
            aria-label="Solve"
          >
            <IconCircleCheck size={17} />
          </ActionIcon>
        )}
        {editable && p.solved && (
          <ActionIcon
            variant="subtle"
            color="red"
            onClick={() => onUnsolve(p)}
            data-tip="Mark unsolved"
            aria-label="Unsolve"
          >
            <IconCircleX size={17} />
          </ActionIcon>
        )}
        {huntUid && sheetUrl && extensionSupports("work") && (
          <ActionIcon
            variant="subtle"
            color="blue"
            onClick={() => workOn({ id: p.id, sheet_url: sheetUrl, hunt_uid: huntUid })}
            data-tip="Work on this puzzle"
            aria-label="Work on this puzzle"
          >
            <IconPlayerPlay size={16} />
          </ActionIcon>
        )}
        {sheetUrl && puzzleUrl && extensionSupports("openPuzzle") && (
          <ActionIcon
            variant="subtle"
            color="blue"
            onClick={() => openPuzzle(p.name, sheetUrl, puzzleUrl)}
            data-tip="Open puzzle as a tab group"
            aria-label="Open puzzle as a tab group"
          >
            <IconBrowserPlus size={16} />
          </ActionIcon>
        )}
        <Indicator label={p.notes.length} size={16} disabled={p.notes.length === 0} color="blue">
          <ActionIcon
            variant="subtle"
            color="gray"
            onClick={() => onOpenNotes(p)}
            data-tip="Notes"
            aria-label="Notes"
          >
            <IconNote size={16} />
          </ActionIcon>
        </Indicator>
        {p.rollup_stale && (
          <Tooltip label={p.rollup_error || "This minimeta's sheet is out of date"} multiline maw={320}>
            <Badge
              color="red"
              variant="light"
              size="sm"
              leftSection={<IconAlertTriangle size={12} />}
              style={{ cursor: "default" }}
            >
              sheet stale
            </Badge>
          </Tooltip>
        )}
      </Group>
    </Table.Td>
  );
});

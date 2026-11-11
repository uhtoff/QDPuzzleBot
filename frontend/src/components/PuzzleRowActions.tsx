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
import { extensionPresent, openPuzzle, workOn } from "../lib/extension";

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
export function PuzzleRowActions({
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
        {huntUid && sheetUrl && extensionPresent() && (
          <Tooltip label="Work on this puzzle">
            <ActionIcon
              variant="subtle"
              color="blue"
              onClick={() => workOn({ id: p.id, sheet_url: sheetUrl, hunt_uid: huntUid })}
              aria-label="Work on this puzzle"
            >
              <IconPlayerPlay size={16} />
            </ActionIcon>
          </Tooltip>
        )}
        {sheetUrl && puzzleUrl && extensionPresent() && (
          <Tooltip label="Open puzzle as a tab group">
            <ActionIcon
              variant="subtle"
              color="blue"
              onClick={() => openPuzzle(p.name, sheetUrl, puzzleUrl)}
              aria-label="Open puzzle as a tab group"
            >
              <IconBrowserPlus size={16} />
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

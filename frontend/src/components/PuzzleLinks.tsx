import { ActionIcon, Group, Indicator, Tooltip } from "@mantine/core";
import { IconBrandDiscord, IconExternalLink, IconFileSpreadsheet } from "@tabler/icons-react";
import type { Puzzle } from "../types";

interface Props {
  puzzle: Puzzle;
  sheetBase: string | null;
  archiveSheetBase: string | null;
}

export function PuzzleLinks({ puzzle, sheetBase, archiveSheetBase }: Props) {
  const base = puzzle.archived && archiveSheetBase ? archiveSheetBase : sheetBase;
  const sheetUrl = base && puzzle.sheet_page ? `${base}${puzzle.sheet_page}` : null;

  return (
    <Group gap={4} wrap="nowrap">
      {sheetUrl && (
        <Tooltip label="Spreadsheet">
          <Indicator
            label={puzzle.sheets.length}
            size={16}
            disabled={puzzle.sheets.length === 0}
            color="green"
          >
            <ActionIcon component="a" href={sheetUrl} target="_blank" variant="subtle" color="green">
              <IconFileSpreadsheet size={16} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      )}
      {puzzle.discord_url && (
        <Tooltip label="Discord channel">
          <ActionIcon component="a" href={puzzle.discord_url} target="_blank" variant="subtle" color="indigo">
            <IconBrandDiscord size={16} />
          </ActionIcon>
        </Tooltip>
      )}
      {puzzle.url && (
        <Tooltip label="Puzzle page">
          <ActionIcon component="a" href={puzzle.url} target="_blank" variant="subtle" color="gray">
            <IconExternalLink size={16} />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}

import { memo } from "react";
import { ActionIcon, Group, Indicator } from "@mantine/core";
import { IconBrandDiscord, IconExternalLink, IconFileSpreadsheet } from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { openPageViaExtension } from "../lib/extension";

interface Props {
  puzzle: Puzzle;
  onOpenChat: (puzzle: Puzzle) => void;
}

export const PuzzleLinks = memo(function PuzzleLinks({ puzzle, onOpenChat }: Props) {
  const sheetUrl = puzzle.sheet_url;
  const pageUrl = puzzle.url;

  return (
    <Group gap={4} wrap="nowrap">
      {sheetUrl && (
        <Indicator label={puzzle.sheets.length} size={16} disabled={puzzle.sheets.length === 0} color="green">
          <ActionIcon
            component="a"
            href={sheetUrl}
            target="_blank"
            variant="subtle"
            color="green"
            title="Spreadsheet"
            aria-label="Spreadsheet"
          >
            <IconFileSpreadsheet size={16} />
          </ActionIcon>
        </Indicator>
      )}
      {puzzle.channel_id && (
        <ActionIcon
          variant="subtle"
          color="indigo"
          onClick={() => onOpenChat(puzzle)}
          title="Chat"
          aria-label="Chat"
        >
          <IconBrandDiscord size={16} />
        </ActionIcon>
      )}
      {pageUrl && (
        <ActionIcon
          component="a"
          href={pageUrl}
          target="_blank"
          variant="subtle"
          color="gray"
          title="Puzzle page"
          aria-label="Puzzle page"
          onClick={(e) => openPageViaExtension(e, sheetUrl, pageUrl)}
        >
          <IconExternalLink size={16} />
        </ActionIcon>
      )}
    </Group>
  );
});

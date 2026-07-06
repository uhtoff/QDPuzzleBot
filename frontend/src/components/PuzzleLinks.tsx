import { useState } from "react";
import { ActionIcon, Group, Indicator, Tooltip } from "@mantine/core";
import { IconBrandDiscord, IconExternalLink, IconFileSpreadsheet } from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { ChatViewer } from "./ChatViewer";

interface Props {
  puzzle: Puzzle;
  sheetBase: string | null;
  solvedSheetBase: string | null;
  huntArchived: boolean;
}

export function PuzzleLinks({ puzzle, sheetBase, solvedSheetBase, huntArchived }: Props) {
  const base = puzzle.on_solved_sheet && solvedSheetBase ? solvedSheetBase : sheetBase;
  const sheetUrl = base && puzzle.sheet_page ? `${base}${puzzle.sheet_page}` : null;
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <Group gap={4} wrap="nowrap">
      {sheetUrl && (
        <Tooltip label="Spreadsheet">
          <Indicator label={puzzle.sheets.length} size={16} disabled={puzzle.sheets.length === 0} color="green">
            <ActionIcon component="a" href={sheetUrl} target="_blank" variant="subtle" color="green">
              <IconFileSpreadsheet size={16} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      )}
      {puzzle.channel_id && (
        <>
          <Tooltip label="Chat">
            <ActionIcon variant="subtle" color="indigo" onClick={() => setChatOpen(true)}>
              <IconBrandDiscord size={16} />
            </ActionIcon>
          </Tooltip>
          <ChatViewer
            opened={chatOpen}
            onClose={() => setChatOpen(false)}
            channelId={puzzle.channel_id}
            live={!huntArchived}
            title={puzzle.name}
            discordDeepUrl={puzzle.discord_deep_url}
            discordUrl={puzzle.discord_url}
          />
        </>
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

import { ActionIcon, Anchor, Button, Group, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import {
  IconExternalLink,
  IconEye,
  IconEyeOff,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import type { HuntSheet } from "../types";
import { useAddHuntSheet, useRemoveHuntSheet } from "../api";
import { notifyError } from "../util";

interface HuntInfoPanelProps {
  uid: string;
  url: string;
  start_timestamp: number | null;
  sheet_base_url: string | null;
  solved_sheet_base_url: string | null;
  hunt_sheets: HuntSheet[];
  /** False for an archived hunt — read-only, exactly as the puzzle drawer is. */
  editable: boolean;
  username: string;
  password: string;
}

export function HuntInfoPanel({
  uid,
  url,
  start_timestamp,
  sheet_base_url,
  solved_sheet_base_url,
  hunt_sheets,
  editable,
  username,
  password,
}: HuntInfoPanelProps) {
  const [revealPassword, setRevealPassword] = useState(false);
  const [sheetName, setSheetName] = useState("");
  const addHuntSheet = useAddHuntSheet(uid);
  const removeHuntSheet = useRemoveHuntSheet(uid);

  function submitSheet() {
    // The name is optional — the service falls back to "Sheet for <hunt>",
    // mirroring !add_sheet with no argument.
    addHuntSheet.mutate(
      { name: sheetName.trim() },
      { onSuccess: () => setSheetName(""), onError: notifyError },
    );
  }

  const startTimeStr =
    start_timestamp != null && start_timestamp > 0
      ? new Date(start_timestamp * 1000).toLocaleString()
      : null;

  return (
    <Stack gap="sm">
      {url && (
        <Group gap={6}>
          <IconExternalLink size={14} />
          <Text size="sm">
            Website:{" "}
            <Anchor href={url} target="_blank" inherit>
              {url}
            </Anchor>
          </Text>
        </Group>
      )}

      {startTimeStr && (
        <Text size="sm">Start time: {startTimeStr}</Text>
      )}

      {sheet_base_url && (
        <Group gap={6}>
          <IconExternalLink size={14} />
          <Text size="sm">
            Google Sheet:{" "}
            <Anchor href={sheet_base_url} target="_blank" inherit>
              Open sheet
            </Anchor>
          </Text>
        </Group>
      )}

      {solved_sheet_base_url && (
        <Group gap={6}>
          <IconExternalLink size={14} />
          <Text size="sm">
            Solved puzzles sheet:{" "}
            <Anchor href={solved_sheet_base_url} target="_blank" inherit>
              Open sheet
            </Anchor>
          </Text>
        </Group>
      )}

      {/* The hunt's own working sheets — somewhere to pick at something found in
          the hunt's alt text before anyone knows which puzzle it belongs to.
          Sits with the sheet links because that is what it is: another sheet for
          this hunt. Mirrors the puzzle drawer's Sheets section, and is the web
          equivalent of !add_sheet in the hunt's general channel. */}
      {(hunt_sheets.length > 0 || editable) && (
        <Stack gap={4}>
          <Text size="sm" fw={500}>Hunt working sheets</Text>
          {hunt_sheets.length === 0 && (
            <Text size="sm" c="dimmed">None yet.</Text>
          )}
          {hunt_sheets.map((s) => {
            const sheetUrl = sheet_base_url ? `${sheet_base_url}${s.page_id ?? ""}` : null;
            return (
              <Group key={s.id} gap={6} wrap="nowrap">
                <IconExternalLink size={14} />
                {sheetUrl ? (
                  <Anchor href={sheetUrl} target="_blank" size="sm" lineClamp={1}
                          style={{ flex: 1 }}>
                    {s.name}
                  </Anchor>
                ) : (
                  <Text size="sm" lineClamp={1} style={{ flex: 1 }}>{s.name}</Text>
                )}
                {editable && (
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() =>
                      removeHuntSheet.mutate({ sheetId: s.id }, { onError: notifyError })
                    }
                    loading={removeHuntSheet.isPending}
                    aria-label={`Remove hunt sheet ${s.name}`}
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                )}
              </Group>
            );
          })}
          {editable && (
            <Group gap="xs" wrap="nowrap">
              <TextInput
                size="xs"
                placeholder="New hunt sheet name (optional)"
                value={sheetName}
                onChange={(e) => setSheetName(e.currentTarget.value)}
                onKeyDown={(e) => e.key === "Enter" && submitSheet()}
                style={{ flex: 1, maxWidth: 320 }}
              />
              <Button
                size="xs"
                variant="light"
                leftSection={<IconPlus size={14} />}
                onClick={submitSheet}
                loading={addHuntSheet.isPending}
              >
                Add
              </Button>
            </Group>
          )}
        </Stack>
      )}

      {username && (
        <Text size="sm">Username: {username}</Text>
      )}

      {password && (
        <Group gap={6}>
          <Text size="sm" style={{ flex: 1, wordBreak: "break-all" }}>
            Password: {revealPassword ? password : "••••••••"}
          </Text>
          <Button
            variant="subtle"
            size="xs"
            onClick={() => setRevealPassword((v) => !v)}
            leftSection={revealPassword ? <IconEyeOff size={14} /> : <IconEye size={14} />}
          >
            {revealPassword ? "Hide" : "Show"}
          </Button>
        </Group>
      )}

      {!url && !startTimeStr && !sheet_base_url && !solved_sheet_base_url && !username && !password
        && hunt_sheets.length === 0
        && <Text size="sm" c="dimmed">No hunt settings configured yet.</Text>
      }
    </Stack>
  );
}

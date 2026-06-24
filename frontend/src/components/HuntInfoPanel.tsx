import { Anchor, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { IconExternalLink, IconEye, IconEyeOff } from "@tabler/icons-react";

interface HuntInfoPanelProps {
  url: string;
  start_timestamp: number | null;
  sheet_base_url: string | null;
  archive_sheet_base_url: string | null;
  username: string;
  password: string;
}

export function HuntInfoPanel({
  url,
  start_timestamp,
  sheet_base_url,
  archive_sheet_base_url,
  username,
  password,
}: HuntInfoPanelProps) {
  const [revealPassword, setRevealPassword] = useState(false);

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

      {archive_sheet_base_url && (
        <Group gap={6}>
          <IconExternalLink size={14} />
          <Text size="sm">
            Archive Sheet:{" "}
            <Anchor href={archive_sheet_base_url} target="_blank" inherit>
              Open archive sheet
            </Anchor>
          </Text>
        </Group>
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

      {!url && !startTimeStr && !sheet_base_url && !archive_sheet_base_url && !username && !password
        && <Text size="sm" c="dimmed">No hunt settings configured yet.</Text>
      }
    </Stack>
  );
}

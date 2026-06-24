import { notifications } from "@mantine/notifications";
import {
  Alert,
  Badge,
  Button,
  Collapse,
  Divider,
  Group,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useState } from "react";
import {
  IconAlertTriangle,
  IconArchive,
  IconChevronDown,
  IconChevronUp,
  IconFileSpreadsheet,
} from "@tabler/icons-react";
import { TypedConfirmModal } from "./TypedConfirmModal";
import { useArchiveHunt, useCreateArchiveSheet, useUpdateHuntSettings, type HuntSettingsPatch } from "../api";

interface AdminSettingsPanelProps {
  uid: string;
  url: string;
  start_timestamp: number | null;
  username: string;
  password: string;
  url_sep: string;
  puzzle_prefix: string;
  archived: boolean;
  hasArchiveSheet: boolean;
}

interface FormValues {
  url: string;
  start_local: string;  // datetime-local input format
  username: string;
  password: string;
  url_sep: string;
  puzzle_prefix: string;
}

export function AdminSettingsPanel({
  uid,
  url,
  start_timestamp,
  username,
  password,
  url_sep,
  puzzle_prefix,
  archived,
  hasArchiveSheet,
}: AdminSettingsPanelProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);

  const settingsMut = useUpdateHuntSettings(uid);
  const archiveMut = useArchiveHunt(uid);
  const sheetMut = useCreateArchiveSheet(uid);

  const startLocalDefault =
    start_timestamp != null && start_timestamp > 0
      ? new Date(start_timestamp * 1000).toISOString().slice(0, 16)
      : "";

  const [values, setValues] = useState<FormValues>({
    url,
    start_local: startLocalDefault,
    username,
    password,
    url_sep,
    puzzle_prefix,
  });

  // Sync form when parent data changes
  if (values.url !== url || values.start_local !== startLocalDefault) {
    setValues({
      url,
      start_local: startLocalDefault,
      username,
      password,
      url_sep,
      puzzle_prefix,
    });
  }

  const handleSave = useCallback(() => {
    const patch: HuntSettingsPatch = {};

    if (values.url !== url) {
      patch.url = values.url;
    }

    if (values.start_local) {
      const ts = Math.round(new Date(values.start_local).getTime() / 1000);
      if (ts !== start_timestamp) {
        patch.start_timestamp = ts;
      }
    } else if (start_timestamp != null && start_timestamp > 0) {
      patch.start_timestamp = 0;
    }

    if (values.username !== username) {
      patch.username = values.username;
    }
    if (values.password !== password) {
      patch.password = values.password;
    }
    if (values.url_sep !== url_sep) {
      patch.url_sep = values.url_sep;
    }
    if (values.puzzle_prefix !== puzzle_prefix) {
      patch.puzzle_prefix = values.puzzle_prefix;
    }

    const changedKeys = Object.keys(patch);
    if (changedKeys.length === 0) {
      notifications.show({ message: "No changes to save.", color: "gray" });
      return;
    }

    settingsMut.mutate(patch, {
      onSuccess: () => {
        notifications.show({
          message: `Saved ${changedKeys.join(", ")}`,
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [values, url, start_timestamp, username, password, url_sep, puzzle_prefix, settingsMut]);

  const handleToggleArchive = useCallback(() => {
    archiveMut.mutate(!archived, {
      onSuccess: () => {
        notifications.show({
          message: archived ? "Hunt unarchived." : "Hunt archived.",
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [archived, archiveMut]);

  const handleCreateArchiveSheet = useCallback(() => {
    sheetMut.mutate(undefined, {
      onSuccess: () => {
        notifications.show({
          message: "Archive sheet created.",
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [sheetMut]);

  return (
    <Stack gap="md">
      <Title order={4}>Hunt Settings</Title>
      <Text size="sm" c="dimmed">
        Edit per-hunt settings. Only changed fields are saved when you press Save.
      </Text>

      {settingsMut.isError && (
        <Alert icon={<IconAlertTriangle size={16} />} color="red" title="Error">
          {(settingsMut.error as Error).message}
        </Alert>
      )}

      <Stack gap="sm">
        <TextInput
          label="Website URL"
          placeholder="https://example.com/"
          value={values.url}
          onChange={(e) => setValues((v) => ({ ...v, url: e.target.value }))}
        />

        <TextInput
          type="datetime-local"
          label="Start time"
          description="Leaving this blank clears the start time."
          value={values.start_local}
          onChange={(e) => setValues((v) => ({ ...v, start_local: e.target.value }))}
        />

        <TextInput
          label="Shared username"
          placeholder="team-name"
          value={values.username}
          onChange={(e) => setValues((v) => ({ ...v, username: e.target.value }))}
        />

        <TextInput
          type="password"
          label="Shared password"
          value={values.password}
          onChange={(e) => setValues((v) => ({ ...v, password: e.target.value }))}
        />

        <Divider />

        <Group onClick={() => setAdvancedOpen((o) => !o)} style={{ cursor: "pointer" }}>
          {advancedOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
          <Text size="sm" fw={600}>Advanced</Text>
        </Group>

        <Collapse in={advancedOpen}>
          <Stack gap="sm" pb="sm">
            <TextInput
              label="URL separator"
              description="Used to build puzzle URLs (e.g. puzzle-1, puzzles_2)."
              placeholder="-"
              value={values.url_sep}
              onChange={(e) => setValues((v) => ({ ...v, url_sep: e.target.value }))}
            />

            <TextInput
              label="Puzzle prefix"
              description="Prefix embedded in puzzle URLs."
              placeholder="puzzle"
              value={values.puzzle_prefix}
              onChange={(e) => setValues((v) => ({ ...v, puzzle_prefix: e.target.value }))}
            />
          </Stack>
        </Collapse>

        <Group gap="sm">
          <Button onClick={handleSave} loading={settingsMut.isPending}>
            Save settings
          </Button>
        </Group>
      </Stack>

      <Divider />

      {/* Archive actions */}
      <Stack gap="sm">
        <Title order={5}>Archive</Title>

        <Group>
          <Badge color={archived ? "gray" : "green"} size="sm">
            Currently: {archived ? "Archived" : "Active"}
          </Badge>
          <Button
            variant="light"
            size="xs"
            leftSection={<IconArchive size={14} />}
            onClick={() => setArchiveConfirmOpen(true)}
            loading={archiveMut.isPending}
          >
            {archived ? "Unarchive" : "Archive"} hunt
          </Button>
        </Group>

        {!hasArchiveSheet && (
          <Button
            size="xs"
            variant="light"
            leftSection={<IconFileSpreadsheet size={14} />}
            onClick={handleCreateArchiveSheet}
            loading={sheetMut.isPending}
          >
            Create archive sheet
          </Button>
        )}

        {hasArchiveSheet && (
          <Text size="sm" c="green">✓ Archive sheet already created.</Text>
        )}
      </Stack>

      <TypedConfirmModal
        opened={archiveConfirmOpen}
        onClose={() => setArchiveConfirmOpen(false)}
        onConfirm={handleToggleArchive}
        title={archived ? "Unarchive hunt" : "Archive hunt"}
        confirmPhrase={archived ? "unarchive" : "archive"}
        confirmLabel={archived ? "Unarchive" : "Archive"}
        loading={archiveMut.isPending}
      >
        <Text size="sm" c={archived ? "yellow" : "red"}>
          {archived
            ? "Are you sure you want to unarchive this hunt? It will become active and editable again."
            : "⚠️ This will archive the hunt, making it read-only for all users."}
        </Text>
      </TypedConfirmModal>
    </Stack>
  );
}

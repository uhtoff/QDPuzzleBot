import { notifications } from "@mantine/notifications";
import {
  Alert,
  Badge,
  Button,
  Collapse,
  Divider,
  Group,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  IconAlertTriangle,
  IconArchive,
  IconChevronDown,
  IconChevronUp,
  IconFileSpreadsheet,
  IconTrash,
} from "@tabler/icons-react";
import { MarkCompleteModal } from "./MarkCompleteModal";
import { TypedConfirmModal } from "./TypedConfirmModal";
import { TeamArchiveModal } from "./TeamArchiveModal";
import { useArchiveHunt, useCompleteHunt, useCreateSolvedSheet, useDeleteHunt, useSetSheetMode, useUpdateHuntSettings, type HuntSettingsPatch } from "../api";
import { UTC_OFFSET_OPTIONS, browserOffsetMinutes, localToTs, tsToLocal } from "../util";

interface AdminSettingsPanelProps {
  uid: string;
  huntName: string;
  url: string;
  start_timestamp: number | null;
  username: string;
  password: string;
  url_sep: string;
  puzzle_prefix: string;
  archived: boolean;
  isTeamHunt: boolean;
  multiTeam: { name: string; completed: boolean; archived: boolean } | null;
  hasSolvedSheet: boolean;
  sheetMode: "tabs" | "files";
  puzzleCount: number;
}

interface FormValues {
  url: string;
  start_local: string;  // datetime-local input format, wall-clock time in start_offset_minutes
  start_offset_minutes: number;  // UTC offset (minutes east of UTC) start_local is expressed in
  username: string;
  password: string;
  url_sep: string;
  puzzle_prefix: string;
}

export function AdminSettingsPanel({
  uid,
  huntName,
  url,
  start_timestamp,
  username,
  password,
  url_sep,
  puzzle_prefix,
  archived,
  isTeamHunt,
  multiTeam,
  hasSolvedSheet,
  sheetMode,
  puzzleCount,
}: AdminSettingsPanelProps) {
  const navigate = useNavigate();
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);
  const [completeConfirmOpen, setCompleteConfirmOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const settingsMut = useUpdateHuntSettings(uid);
  const archiveMut = useArchiveHunt(uid);
  const sheetMut = useCreateSolvedSheet(uid);
  const modeMut = useSetSheetMode(uid);
  const deleteMut = useDeleteHunt(uid);
  const completeMut = useCompleteHunt(uid);

  const [values, setValues] = useState<FormValues>(() => {
    const offset = browserOffsetMinutes();
    return {
      url,
      start_local: tsToLocal(start_timestamp, offset),
      start_offset_minutes: offset,
      username,
      password,
      url_sep,
      puzzle_prefix,
    };
  });

  // Re-sync the form from the server only when we switch to a different hunt —
  // not on every background refetch of the SAME hunt (the dashboard polls every
  // 30s), which would otherwise wipe out-of-sync in-progress edits every time it
  // fired. See #7/#8 in docs/reviews/2026-07-02-staging-full-review.md.
  const lastUid = useRef(uid);
  useEffect(() => {
    if (lastUid.current === uid) return;
    lastUid.current = uid;
    const offset = browserOffsetMinutes();
    setValues({
      url,
      start_local: tsToLocal(start_timestamp, offset),
      start_offset_minutes: offset,
      username,
      password,
      url_sep,
      puzzle_prefix,
    });
  }, [uid, url, start_timestamp, username, password, url_sep, puzzle_prefix]);

  const handleSave = useCallback(() => {
    const patch: HuntSettingsPatch = {};

    if (values.url !== url) {
      patch.url = values.url;
    }

    if (values.start_local) {
      const ts = localToTs(values.start_local, values.start_offset_minutes);
      if (ts !== null && ts !== start_timestamp) {
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

  const handleToggleComplete = (complete: boolean) => {
    completeMut.mutate(complete, {
      onSuccess: (r) => {
        setCompleteConfirmOpen(false);
        notifications.show({ message: r.message, color: "green" });
      },
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  };

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

  const handleCreateSolvedSheet = useCallback(() => {
    sheetMut.mutate(undefined, {
      onSuccess: () => {
        notifications.show({
          message: "Solved puzzles sheet created.",
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [sheetMut]);

  const handleToggleSheetMode = useCallback(() => {
    const next = sheetMode === "files" ? "tabs" : "files";
    modeMut.mutate(next, {
      onSuccess: () => {
        notifications.show({ message: `Sheet mode is now "${next}".`, color: "green" });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [sheetMode, modeMut]);

  const handleDelete = useCallback(() => {
    deleteMut.mutate(undefined, {
      onSuccess: () => {
        notifications.show({ message: `Hunt "${huntName}" deleted.`, color: "green" });
        navigate("/");
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [deleteMut, huntName, navigate]);

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

        <Group align="flex-end" gap="sm">
          <TextInput
            type="datetime-local"
            label="Start time"
            description="Leaving this blank clears the start time."
            value={values.start_local}
            onChange={(e) => setValues((v) => ({ ...v, start_local: e.target.value }))}
            style={{ flex: 1 }}
          />
          <Select
            label="Timezone"
            data={UTC_OFFSET_OPTIONS}
            value={String(values.start_offset_minutes)}
            onChange={(val) =>
              val != null &&
              setValues((v) => ({ ...v, start_offset_minutes: Number(val) }))
            }
            allowDeselect={false}
            w={140}
          />
        </Group>

        <TextInput
          label="Shared username"
          placeholder="team-name"
          value={values.username}
          onChange={(e) => setValues((v) => ({ ...v, username: e.target.value }))}
        />

        <TextInput
          autoComplete="off"
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

        {multiTeam && (
          <Switch
            label="Finished the hunt"
            description={`Part of the multi-team hunt ${multiTeam.name}.`}
            checked={multiTeam.completed}
            disabled={archived || multiTeam.archived || completeMut.isPending}
            onChange={(e) => {
              // Marking complete opens spoilers to the team, so it needs a confirm;
              // undoing it does not.
              if (e.currentTarget.checked) setCompleteConfirmOpen(true);
              else handleToggleComplete(false);
            }}
          />
        )}
        {multiTeam && (
          <MarkCompleteModal
            opened={completeConfirmOpen}
            team={huntName}
            loading={completeMut.isPending}
            onClose={() => setCompleteConfirmOpen(false)}
            onConfirm={() => handleToggleComplete(true)}
          />
        )}

        <Group>
          <Badge color={sheetMode === "files" ? "blue" : "gray"} size="sm">
            Sheets: {sheetMode === "files" ? "one spreadsheet per puzzle" : "tabs in one spreadsheet"}
          </Badge>
          {puzzleCount === 0 && (
            <Button
              size="xs"
              variant="light"
              onClick={handleToggleSheetMode}
              loading={modeMut.isPending}
            >
              {sheetMode === "files" ? "Use tabs instead" : "Use one spreadsheet per puzzle"}
            </Button>
          )}
        </Group>

        {sheetMode === "tabs" && !hasSolvedSheet && (
          <Button
            size="xs"
            variant="light"
            leftSection={<IconFileSpreadsheet size={14} />}
            onClick={handleCreateSolvedSheet}
            loading={sheetMut.isPending}
          >
            Move solved feeders to their own sheet
          </Button>
        )}

        {sheetMode === "tabs" && hasSolvedSheet && (
          <Text size="sm" c="green">✓ Solved puzzles sheet already created.</Text>
        )}
      </Stack>

      {isTeamHunt && !archived && (
        <TeamArchiveModal
          uid={uid}
          huntName={huntName}
          opened={archiveConfirmOpen}
          onClose={() => setArchiveConfirmOpen(false)}
        />
      )}
      <TypedConfirmModal
        opened={archiveConfirmOpen && !(isTeamHunt && !archived)}
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

      <Divider />

      {/* Delete danger zone */}
      <Stack gap="sm">
        <Title order={5} c="red">Danger zone</Title>
        <Button
          color="red"
          variant="light"
          size="xs"
          leftSection={<IconTrash size={14} />}
          onClick={() => setDeleteConfirmOpen(true)}
          loading={deleteMut.isPending}
        >
          Delete hunt
        </Button>
      </Stack>

      <TypedConfirmModal
        opened={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        onConfirm={handleDelete}
        title="Delete hunt permanently"
        confirmPhrase={huntName}
        confirmLabel="Delete permanently"
        loading={deleteMut.isPending}
      >
        <Stack gap="xs">
          <Text size="sm" c="red" fw={600}>
            This is irreversible. The following will be permanently deleted:
          </Text>
          <Text size="sm">• {puzzleCount} puzzle(s), all rounds, all Discord channels and categories</Text>
          <Text size="sm">• Google spreadsheets — permanently deleted from Google Drive, not recoverable</Text>
        </Stack>
      </TypedConfirmModal>
    </Stack>
  );
}
